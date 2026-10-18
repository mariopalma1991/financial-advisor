"""Paper portfolio from the factor scores against VOO, in MXN (rules in
src/transforms/paper.py, settings in config/paper_portfolio.yaml).

Two books, written to paper_decisions, paper_fills and paper_nav:
  forward_top20   starts on forward_start; each signal's decisions are stored the
                  first time they are made and reused as stored on every later run
  backtest_top20  the same rules from backtest_start, recomputed every run (an
                  in-sample check: the factors were designed looking at this period)
Fills and NAV are recomputed from prices each run (fills are next-day closes, so
the result is the same whenever it is computed). Runs after compute_factors.
  python -m src.jobs.paper_portfolio
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import psycopg2.extras
import yaml

from src.mcp_server.tools import round_trip_cost
from src.quality.run_metadata import current_run_id, job_run
from src.storage.supabase_client import pg_connection
from src.transforms import factors as fx
from src.transforms import paper

_ROOT = Path(__file__).resolve().parents[2]
FORWARD, BACKTEST = "forward_top20", "backtest_top20"
FALLBACK_BENCHMARK = "SPY"   # same index as VOO; used until VOO's history covers the start


def load_rules() -> tuple[paper.Rules, dict]:
    cfg = yaml.safe_load((_ROOT / "config/paper_portfolio.yaml").read_text())
    costs = round_trip_cost(yaml.safe_load((_ROOT / "config/cost_model.yaml").read_text()), sic=True)
    p, t = cfg["portfolio"], cfg["taxes"]
    rules = paper.Rules(n=p["n"], keep_rank=p["keep_rank"], max_per_industry=p["max_per_industry"],
                        cost_per_side=costs["round_trip"] / 2, initial_mxn=float(p["initial_mxn"]),
                        isr_on_gains=t["isr_on_gains"], dividend_withholding=t["dividend_withholding"])
    return rules, p


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _rows(cur, sql: str, params=None) -> list[dict]:
    cur.execute(sql, params or {})
    return [dict(r) for r in cur.fetchall()]


def signal_days(factor_days: list[str], start: str, latest_bar: str) -> list[str]:
    """The first factor day on or after start, then each later month's last factor
    day, for months that have ended (the month of latest_bar is still open)."""
    days = sorted(d for d in factor_days if d >= start)
    if not days:
        return []
    month_end: dict[str, str] = {}
    for d in days:
        month_end[d[:7]] = max(month_end.get(d[:7], d), d)
    ends = [d for m, d in month_end.items() if m < latest_bar[:7] and d > days[0]]
    return [days[0]] + sorted(ends)


def load_market(cur, since: str) -> dict:
    bars: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(cur, "select distinct on (ticker, bar_date) ticker, bar_date, close, ingested_at "
                        "from price_bars_daily where bar_date >= %(s)s::date - 10 and close > 0 "
                        "order by ticker, bar_date, ingested_at desc", {"s": since}):
        bars[r["ticker"]].append({"bar_date": _iso(r["bar_date"]), "close": float(r["close"]),
                                  "ingested_at": _iso(r["ingested_at"])})
    actions: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"split": [], "dividend": []})
    for r in _rows(cur, "select distinct on (ticker, action_type, ex_date) ticker, action_type, ex_date, "
                        "amount, ratio, ingested_at from raw_corporate_actions "
                        "where action_type in ('split', 'dividend') "
                        "order by ticker, action_type, ex_date, ingested_at desc"):
        actions[r["ticker"]][r["action_type"]].append(
            {"ex_date": _iso(r["ex_date"]), "ratio": float(r["ratio"] or 0) or None,
             "amount": float(r["amount"] or 0), "ingested_at": _iso(r["ingested_at"])})
    closes = {}
    for t, b in bars.items():
        adj = fx.adjust_for_splits(b, actions[t]["split"])
        closes[t] = paper.Series([(x["bar_date"], c) for x, c in zip(b, adj)])
    divs = {t: paper.adjust_dividends([d for d in a["dividend"] if d["amount"] > 0], a["split"])
            for t, a in actions.items()}
    usd_mxn = paper.Series([(_iso(r["obs_date"]), float(r["value"])) for r in _rows(
        cur, "select distinct on (obs_date) obs_date, value from macro_observations "
             "where series_id = 'DEXMXUS' and value > 0 order by obs_date, ingested_at desc")])
    return {"closes": closes, "divs": divs, "fx": usd_mxn}


def scores_on(cur, day: str) -> dict[str, dict]:
    return {r["ticker"]: {"rank": r["rank"], "eligible": r["eligible"], "industry": r["industry"],
                          "score": r["score"]}
            for r in _rows(cur, "select ticker, rank, eligible, industry, score from factor_scores "
                                "where as_of = %(d)s", {"d": day})}


def run_book(cur, name: str, signals: list[str], frozen: dict[str, list[dict]], market: dict,
             days: list[str], rules: paper.Rules) -> dict:
    def px(t: str, day: str):
        s = market["closes"].get(t)
        return s.at(day) if s else None

    def decide(sd: str, held: list[str]) -> list[dict]:
        return frozen.get(sd) or paper.select_targets(held, scores_on(cur, sd), rules)

    return paper.simulate(days, signals, decide, px, market["fx"], market["divs"], rules)


def run_benchmark(first_signal: str, bench: str, market: dict, days: list[str], rules: paper.Rules) -> dict:
    def px(t: str, day: str):
        return market["closes"][bench].at(day)

    one = paper.Rules(**{**rules.__dict__, "n": 1})
    return paper.simulate(days, [first_signal], lambda sd, held: [{"ticker": bench, "action": "buy"}],
                          px, market["fx"], market["divs"], one)


def store(cur, name: str, result: dict, bench: dict, bench_ticker: str, rules: paper.Rules,
          new_decisions: dict[str, list[dict]], replace_decisions: bool) -> None:
    run_id = current_run_id()
    if replace_decisions:
        cur.execute("delete from paper_decisions where portfolio = %s", (name,))
    dec = [(name, sd, d["ticker"], d["action"], d.get("reason"), d.get("rank"), d.get("score"),
            d.get("industry"), run_id) for sd, ds in new_decisions.items() for d in ds]
    if dec:
        psycopg2.extras.execute_values(
            cur, "insert into paper_decisions (portfolio, signal_day, ticker, action, reason, rank, score, "
                 "industry, run_id) values %s on conflict do nothing", dec)
    cur.execute("delete from paper_fills where portfolio = %s", (name,))
    fills = [(name, f["exec_day"], f["signal_day"], f["ticker"], f["side"], f["reason"], f["shares"],
              f["price_usd"], f["usd_mxn"], f["amount_mxn"], f["cost_mxn"], f["gain_mxn"], run_id)
             for f in result["fills"]]
    if fills:
        psycopg2.extras.execute_values(
            cur, "insert into paper_fills (portfolio, exec_day, signal_day, ticker, side, reason, shares, "
                 "price_usd, usd_mxn, amount_mxn, cost_mxn, gain_mxn, run_id) values %s", fills)
    cur.execute("delete from paper_nav where portfolio = %s", (name,))
    b = {r["day"]: r for r in bench["nav"]}
    nav = [(name, r["day"], r["nav_mxn"], r["cash_mxn"], r["liquidation_mxn"], r["n_positions"],
            rules.initial_mxn, r["usd_mxn"], bench_ticker, b.get(r["day"], {}).get("nav_mxn"),
            b.get(r["day"], {}).get("liquidation_mxn"), run_id) for r in result["nav"]]
    if nav:
        psycopg2.extras.execute_values(
            cur, "insert into paper_nav (portfolio, day, nav_mxn, cash_mxn, liquidation_mxn, n_positions, "
                 "initial_mxn, usd_mxn, bench_ticker, bench_nav_mxn, bench_liquidation_mxn, run_id) values %s",
            nav)


def main() -> None:
    rules, cfg = load_rules()
    with job_run("paper_portfolio") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            factor_days = [_iso(r["as_of"]) for r in _rows(cur, "select distinct as_of from factor_scores")]
            cur.execute("select max(bar_date) as d from price_bars_daily")
            latest_bar = _iso(cur.fetchone()["d"])
            market = load_market(cur, cfg["backtest_start"])
            bench_ticker = cfg["benchmark"]
            for name, start, keep in ((BACKTEST, cfg["backtest_start"], False), (FORWARD, cfg["forward_start"], True)):
                signals = signal_days(factor_days, start, latest_bar)
                if not signals:
                    em.emit("INFO", "paper_waiting", f"{name}: no factor day on or after {start} yet")
                    continue
                bench = bench_ticker
                vc = market["closes"].get(bench)
                if not vc or not vc.days or vc.days[0] > signals[0]:
                    bench = FALLBACK_BENCHMARK
                days = [d for d in market["closes"][bench].days if d >= signals[0]]
                frozen: dict[str, list[dict]] = defaultdict(list)
                if keep:
                    for r in _rows(cur, "select signal_day, ticker, action, reason, rank, score, industry "
                                        "from paper_decisions where portfolio = %(p)s", {"p": name}):
                        frozen[_iso(r.pop("signal_day"))].append(r)
                result = run_book(cur, name, signals, dict(frozen), market, days, rules)
                bres = run_benchmark(signals[0], bench, market, days, rules)
                new = {sd: ds for sd, ds in result["decisions"].items() if not keep or sd not in frozen}
                store(cur, name, result, bres, bench, rules, new, replace_decisions=not keep)
                conn.commit()
                last = result["nav"][-1] if result["nav"] else None
                blast = bres["nav"][-1] if bres["nav"] else None
                em.emit("INFO", "paper_book", name, payload={
                    "signals": signals, "new_decisions": sorted(new), "fills": len(result["fills"]),
                    "pending": result["pending"], "benchmark": bench,
                    "nav_mxn": last and round(last["nav_mxn"]), "bench_nav_mxn": blast and round(blast["nav_mxn"]),
                    "liquidation_mxn": last and round(last["liquidation_mxn"]),
                    "bench_liquidation_mxn": blast and round(blast["liquidation_mxn"])})


if __name__ == "__main__":
    main()
