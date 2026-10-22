"""One-stock portfolio: hold only the factor #1 and switch when another stock takes the
top, on the paper portfolio's budget, costs and taxes, against VOO, in MXN
(settings in config/top1_portfolio.yaml, rules from config/paper_portfolio.yaml).

Two books (column portfolio): desde_enero, the retrospective one below, and mi_inversion, which
starts from zero on the investor's own deposits (investor_contributions plus the recurring plan, like
the forward book of paper_portfolio) with every factor day from its start a stored signal.

From `start` the desde_enero book is replayed from the month-end rankings; from `live_start` every
factor day is a signal and its decisions are stored once (top1_decisions), because daily
factor_scores are trimmed after about 40 days. Fills and NAV (top1_fills, top1_nav) are
recomputed from prices each run. Reads prices only for the tickers it can hold.
  python -m src.jobs.top1_portfolio
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import psycopg2.extras
import yaml

from src.jobs import paper_portfolio as pp
from src.quality.run_metadata import current_run_id, job_run
from src.storage.supabase_client import pg_connection
from src.transforms import factors as fx
from src.transforms import paper

_ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = 5   # top ranks per signal day whose prices are loaded (the #1 plus fallbacks if one share is too dear)


def load_market(cur, since: str, tickers: list[str]) -> dict:
    """paper_portfolio.load_market for a few tickers (no cold archive: `since` is within the window)."""
    bars: dict[str, list[dict]] = defaultdict(list)
    for r in pp._rows(cur, "select distinct on (ticker, bar_date) ticker, bar_date, close, ingested_at "
                           "from price_bars_daily where ticker = any(%(t)s) and bar_date >= %(s)s::date - 10 and close > 0 "
                           "order by ticker, bar_date, ingested_at desc", {"t": tickers, "s": since}):
        bars[r["ticker"]].append({"bar_date": pp._iso(r["bar_date"]), "close": float(r["close"]),
                                  "ingested_at": pp._iso(r["ingested_at"])})
    actions: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"split": [], "dividend": []})
    for r in pp._rows(cur, "select distinct on (ticker, action_type, ex_date) ticker, action_type, ex_date, "
                           "amount, ratio, ingested_at from raw_corporate_actions "
                           "where ticker = any(%(t)s) and action_type in ('split', 'dividend') "
                           "order by ticker, action_type, ex_date, ingested_at desc", {"t": tickers}):
        actions[r["ticker"]][r["action_type"]].append(
            {"ex_date": pp._iso(r["ex_date"]), "ratio": float(r["ratio"] or 0) or None,
             "amount": float(r["amount"] or 0), "ingested_at": pp._iso(r["ingested_at"])})
    closes = {}
    for t, b in bars.items():
        adj = fx.adjust_for_splits(b, actions[t]["split"])
        closes[t] = paper.Series([(x["bar_date"], c) for x, c in zip(b, adj)])
    divs = {t: paper.adjust_dividends([d for d in a["dividend"] if d["amount"] > 0], a["split"])
            for t, a in actions.items()}
    usd_mxn = paper.Series([(pp._iso(r["obs_date"]), float(r["value"])) for r in pp._rows(
        cur, "select distinct on (obs_date) obs_date, value from macro_observations "
             "where series_id = 'DEXMXUS' and value > 0 order by obs_date, ingested_at desc")])
    return {"closes": closes, "divs": divs, "fx": usd_mxn, "splits": {t: a["split"] for t, a in actions.items()}}


def held_by_day(fills: list[dict]) -> list[tuple[str, str | None]]:
    """(exec_day, ticker held after that day's fills) in order; sells before buys."""
    out: list[tuple[str, str | None]] = []
    cur_t: str | None = None
    for f in sorted(fills, key=lambda f: (f["exec_day"], f["side"] != "sell")):
        if f["side"] == "buy":
            cur_t = f["ticker"]
        elif f["ticker"] == cur_t:
            cur_t = None
        out.append((f["exec_day"], cur_t))
    return out


def store(cur, name: str, result: dict, bench: dict, bench_ticker: str, new_decisions: dict[str, list[dict]]) -> None:
    run_id = current_run_id()
    dec = [(name, sd, d["ticker"], d["action"], d.get("reason"), d.get("rank"), d.get("score"), d.get("industry"), run_id)
           for sd, ds in new_decisions.items() for d in ds]
    if dec:
        psycopg2.extras.execute_values(
            cur, "insert into top1_decisions (portfolio, signal_day, ticker, action, reason, rank, score, industry, run_id) "
                 "values %s on conflict do nothing", dec)
    cur.execute("delete from top1_fills where portfolio = %s", (name,))
    fills = [(name, f["exec_day"], f["signal_day"], f["ticker"], f["side"], f["reason"], f["shares"], f["price_usd"],
              f["usd_mxn"], f["amount_mxn"], f["cost_mxn"], f["gain_mxn"], run_id) for f in result["fills"]]
    if fills:
        psycopg2.extras.execute_values(
            cur, "insert into top1_fills (portfolio, exec_day, signal_day, ticker, side, reason, shares, price_usd, usd_mxn, "
                 "amount_mxn, cost_mxn, gain_mxn, run_id) values %s on conflict do nothing", fills)
    held = held_by_day(result["fills"])
    cur.execute("delete from top1_nav where portfolio = %s", (name,))
    b = {r["day"]: r for r in bench["nav"]}
    nav, i, now_t = [], 0, None
    for r in result["nav"]:
        while i < len(held) and held[i][0] <= r["day"]:
            now_t = held[i][1]
            i += 1
        br = b.get(r["day"], {})
        nav.append((name, r["day"], now_t, r["nav_mxn"], r["cash_mxn"], r["liquidation_mxn"], r["flow_mxn"],
                    r["contributed_mxn"], r["twr"], r["usd_mxn"], bench_ticker, br.get("nav_mxn"),
                    br.get("liquidation_mxn"), br.get("twr"), run_id))
    if nav:
        psycopg2.extras.execute_values(
            cur, "insert into top1_nav (portfolio, day, ticker, nav_mxn, cash_mxn, liquidation_mxn, flow_mxn, contributed_mxn, "
                 "twr, usd_mxn, bench_ticker, bench_nav_mxn, bench_liquidation_mxn, bench_twr, run_id) values %s", nav)


def run_book(cur, em, name: str, rules, cfg: dict, start: str, live: str, factor_days: list[str], latest: str,
             flows_from) -> None:
    """One book: signals (the start's, then month-ends until `live`, every factor day from it), the run, the store.
    flows_from(first_signal) gives the deposits (day, MXN)."""
    signals = sorted(set(pp.signal_days(factor_days, start, latest)) | {d for d in factor_days if d >= live})
    signals = [d for d in signals if d >= start]
    if not signals:
        em.emit("INFO", "top1_waiting", f"{name}: no factor day on or after {start} yet")
        return
    bench = cfg["benchmark"]
    tickers = [r["ticker"] for r in pp._rows(
        cur, "select distinct ticker from factor_scores where as_of = any(%(d)s::date[]) and eligible "
             "and rank <= %(n)s", {"d": signals, "n": CANDIDATES})] + [bench, pp.FALLBACK_BENCHMARK]
    market = load_market(cur, start, tickers)
    vc = market["closes"].get(bench)
    if not vc or not vc.days or vc.days[0] > signals[0]:
        bench = pp.FALLBACK_BENCHMARK
    days = [d for d in market["closes"][bench].days if d >= signals[0]]
    flows = flows_from(signals[0])
    frozen: dict[str, list[dict]] = defaultdict(list)
    for r in pp._rows(cur, "select signal_day, ticker, action, reason, rank, score, industry from top1_decisions "
                           "where portfolio = %(p)s", {"p": name}):
        frozen[pp._iso(r.pop("signal_day"))].append(r)
    result = pp.run_book(cur, signals, dict(frozen), market, days, rules, flows)
    bres = pp.run_benchmark(signals[0], bench, market, days, rules, flows)
    new = {sd: ds for sd, ds in result["decisions"].items() if sd >= live and sd not in frozen}
    store(cur, name, result, bres, bench, new)
    last = result["nav"][-1] if result["nav"] else None
    blast = bres["nav"][-1] if bres["nav"] else None
    em.emit("INFO", "top1_book", name, payload={
        "signals": len(signals), "new_decisions": sorted(new), "fills": len(result["fills"]),
        "pending": result["pending"], "benchmark": bench,
        "nav_mxn": last and round(last["nav_mxn"]), "bench_nav_mxn": blast and round(blast["nav_mxn"])})


def main() -> None:
    rules, cfg = pp.load_rules()
    one = paper.Rules(**{**rules.__dict__, "n": 1, "keep_rank": 1, "max_per_industry": 1, "min_position_mxn": 0.0})
    top = yaml.safe_load((_ROOT / "config/top1_portfolio.yaml").read_text())
    start, live = str(top["start"]), str(top["live_start"])
    plan = (float(cfg.get("recurring_mxn") or 0), cfg.get("recurring_days") or [])
    with job_run("top1_portfolio") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            factor_days = [pp._iso(r["as_of"]) for r in pp._rows(cur, "select distinct as_of from factor_scores")]
            cur.execute("select max(bar_date) as d from price_bars_daily")
            latest = pp._iso(cur.fetchone()["d"])

            def from_january(first: str) -> list[tuple[str, float]]:
                return sorted([(first, float(cfg["initial_mxn"]))] + paper.recurring_flows(first, latest, *plan))

            def from_deposits(first: str) -> list[tuple[str, float]]:
                deposits = pp.contributions(cur, cfg["owner"]) or [(cfg["forward_start"], float(cfg["initial_mxn"]))]
                return sorted(deposits + paper.recurring_flows(cfg["forward_start"], latest, *plan))

            run_book(cur, em, "desde_enero", one, cfg, start, live, factor_days, latest, from_january)
            conn.commit()
            # Mi inversión: from the first factor day on or after its start, every day a stored signal
            run_book(cur, em, "mi_inversion", one, cfg, str(top["mi_inversion_start"]), str(top["mi_inversion_start"]),
                     factor_days, latest, from_deposits)
            conn.commit()


if __name__ == "__main__":
    main()
