"""Daily factor scores for every tracked stock (src/transforms/factors.py) into
factor_scores, one row per ticker and day.

Each run reads only what was known at the close of `as_of` (the same clocks as the
MCP's point-in-time reads), so a past date can be rebuilt without look-ahead:
  python -m src.jobs.compute_factors                     # latest trading day
  python -m src.jobs.compute_factors --as-of 2026-09-30
  python -m src.jobs.compute_factors --backfill-months 12   # month-ends, for tests
Daily rows are kept KEEP_DAILY_DAYS; each month's last row set is kept for good
(monthly rebalancing and forward tests). Runs after every ingest_prices run.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import psycopg2.extras

from src.quality.run_metadata import current_run_id, job_run
from src.storage.supabase_client import pg_connection
from src.transforms import factors as fx

KEEP_DAILY_DAYS = 40
MIN_ADV_USD = 1_000_000
MAX_STALE_DAYS = 7
SURPRISE_MAX_AGE_DAYS = 150   # quarter end within ~5 months: reported in the last ~3
# (the price reaction needs the report date; the earnings calendar starts 2026-09-14)
REPORT_LAG_DAYS = 45
EAR_MAX_AGE_DAYS = 60
MAX_SUE = 0.2
INSIDER_DAYS = 90
INSIDER_SLACK_DAYS = 7

_COLS = ("as_of", "ticker", "industry", "eligible", "last_bar_date", "adv_20d", "mom_12_1",
         "rev_chg", "rev_breadth", "sue", "ear", "ins_buyers", "ins_buy_value", "insider_raw",
         "roe_ttm", "gross_margin", "debt_to_equity", "op_margin_std",
         "z_momentum", "z_revisions", "z_pead", "z_insider", "z_quality",
         "score", "n_factors", "rank", "run_id")


def _rows(cur, sql: str, params: dict) -> list[dict]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def _num(v) -> float | None:
    return None if v is None else float(v)


def _sue(surprise: dict | None, bars: list[dict]) -> float | None:
    """EPS surprise over the price. Above MAX_SUE it is a unit mismatch, not news
    (ADRs whose EPS is in pesos or reais while the price is in dollars)."""
    if not surprise or not bars:
        return None
    v = _num(surprise["surprise"]) / bars[-1]["close"]
    return v if abs(v) <= MAX_SUE else None


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else v


def load_inputs(cur, as_of: date) -> pd.DataFrame:
    """Raw factor inputs per enabled ticker as known at the close of as_of."""
    p = {"day": as_of, "at": datetime.combine(as_of, time(23, 59, 59), timezone.utc)}
    universe = _rows(cur, "select t.ticker, p.industry from current_tickers t "
                          "left join company_profiles p using (ticker) where t.enabled", p)

    bars: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(cur, "select distinct on (ticker, bar_date) ticker, bar_date, close, volume "
                        "from price_bars_daily where bar_date > %(day)s::date - 400 and bar_date <= %(day)s "
                        "and valid_from <= %(at)s and close > 0 "
                        "order by ticker, bar_date, (ingested_at <= %(at)s) desc, ingested_at desc", p):
        bars[r["ticker"]].append({"bar_date": _iso(r["bar_date"]), "close": float(r["close"]),
                                  "volume": float(r["volume"] or 0)})
    splits: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(cur, "select distinct ticker, ex_date, ratio from raw_corporate_actions "
                        "where action_type = 'split' and ratio > 0 and ex_date <= %(day)s", p):
        splits[r["ticker"]].append({"ex_date": _iso(r["ex_date"]), "ratio": float(r["ratio"])})

    trends: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(cur, "select distinct on (ticker, period) * from estimate_trends "
                        "where period in ('0y', '+1y') and snapshot_date <= %(day)s "
                        "and snapshot_date > %(day)s::date - 14 "
                        "order by ticker, period, snapshot_date desc", p):
        trends[r["ticker"]].append(r)

    surprise = {r["ticker"]: r for r in _rows(
        cur, "select distinct on (ticker) ticker, period, surprise from raw_earnings_surprises "
             "where period > %(day)s::date - %(age)s "
             "and (ingested_at <= %(at)s or period + %(lag)s <= %(day)s) and surprise is not null "
             "order by ticker, period desc, (ingested_at <= %(at)s) desc, ingested_at desc",
        {**p, "age": SURPRISE_MAX_AGE_DAYS, "lag": REPORT_LAG_DAYS})}
    report_day = {r["ticker"]: _iso(r["event_date"]) for r in _rows(
        cur, "select ticker, max(event_date) as event_date from raw_earnings_calendar "
             "where event_date <= %(day)s and event_date > %(day)s::date - %(age)s group by ticker",
        {**p, "age": EAR_MAX_AGE_DAYS})}

    ins_start = _rows(cur, "select min(filed_at) as first from insider_trades", p)[0]["first"]
    # Form 4s were loaded from 2026-07-19; a window that starts a few days before still counts
    ins_covered = ins_start is not None and ins_start <= p["at"] - timedelta(days=INSIDER_DAYS - INSIDER_SLACK_DAYS)
    insider = {r["ticker"]: r for r in _rows(
        cur, "select ticker, count(distinct owner) as n_buyers, sum(buy_value) as buy_value "
             "from insider_trades where filed_at <= %(at)s and filed_at > %(at)s - make_interval(days => %(n)s) "
             "and buy_value > 0 and not coalesce(is_10b5_1, false) group by ticker",
        {**p, "n": INSIDER_DAYS})}

    quarters: dict[str, list[dict]] = defaultdict(list)
    for r in _rows(cur, "select distinct on (ticker, period_end) ticker, period_end, roe_ttm, gross_margin, "
                        "debt_to_equity, operating_margin from fundamentals_quarterly "
                        "where period_end > %(day)s::date - 1100 "
                        "and (ingested_at <= %(at)s or period_end + %(lag)s <= %(day)s) "
                        "order by ticker, period_end desc, (ingested_at <= %(at)s) desc, ingested_at desc",
                   {**p, "lag": REPORT_LAG_DAYS}):
        quarters[r["ticker"]].append({"period_end": _iso(r["period_end"]),
                                      **{k: _num(r[k]) for k in ("roe_ttm", "gross_margin", "debt_to_equity",
                                                                 "operating_margin")}})

    out = []
    for u in universe:
        t = u["ticker"]
        b = bars.get(t, [])
        closes = fx.adjust_for_splits(b, splits.get(t, []))
        last = b[-1]["bar_date"] if b else None
        adv = fx.avg_dollar_volume(b)
        fresh = last is not None and (as_of - date.fromisoformat(last)).days <= MAX_STALE_DAYS
        rev_chg, rev_breadth = fx.revision_signals(trends.get(t, []))
        s = surprise.get(t)
        rd = report_day.get(t)
        ins = insider.get(t)
        n_buyers = int(ins["n_buyers"]) if ins else 0
        buy_value = float(ins["buy_value"] or 0) if ins else 0.0
        q = quarters.get(t, [])  # newest first (query order)
        out.append({
            "as_of": as_of.isoformat(), "ticker": t, "industry": u["industry"],
            "eligible": bool(fresh and adv is not None and adv >= MIN_ADV_USD),
            "last_bar_date": last, "adv_20d": adv, "mom_12_1": fx.momentum_12_1(closes),
            "rev_chg": rev_chg, "rev_breadth": rev_breadth,
            # surprise in dollars per share over the price: a percent surprise explodes
            # when the estimate is near zero
            "sue": _sue(s, b),
            "ear": fx.window_return(b, closes, rd) if rd else None,
            "ins_buyers": n_buyers if ins_covered else None,
            "ins_buy_value": buy_value if ins_covered else None,
            "insider_raw": fx.insider_signal(n_buyers, buy_value) if ins_covered else None,
            **fx.quality_inputs(q),
        })
    df = pd.DataFrame(out)
    num = [c for c in df.columns if c not in ("as_of", "ticker", "industry", "eligible", "last_bar_date")]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    return df


def _clean(v):
    if v is None or v is pd.NA:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        return v.item()
    return v


def store(cur, scored: pd.DataFrame) -> int:
    run_id = current_run_id()
    scored = scored.assign(run_id=run_id)
    rows = [tuple(_clean(r[c]) for c in _COLS) for _, r in scored.iterrows()]
    cur.execute("delete from factor_scores where as_of = %s", (scored["as_of"].iloc[0],))
    psycopg2.extras.execute_values(cur, f"insert into factor_scores ({', '.join(_COLS)}) values %s", rows,
                                   page_size=500)
    return len(rows)


def prune(cur) -> None:
    """Drop daily rows older than KEEP_DAILY_DAYS except each month's last day."""
    cur.execute(
        "delete from factor_scores where as_of < current_date - %s and as_of not in "
        "(select max(as_of) from factor_scores group by date_trunc('month', as_of))",
        (KEEP_DAILY_DAYS,))


def latest_bar_day(cur) -> date:
    cur.execute("select max(bar_date) as d from price_bars_daily")
    return cur.fetchone()["d"]


def month_ends(cur, months: int) -> list[date]:
    """Last trading day of each of the last `months` complete months."""
    cur.execute("select max(bar_date) as d from price_bars_daily where bar_date >= "
                "date_trunc('month', current_date) - make_interval(months => %s) "
                "and bar_date < date_trunc('month', current_date) "
                "group by date_trunc('month', bar_date) order by 1", (months,))
    return [r["d"] for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--backfill-months", type=int, default=0)
    args = ap.parse_args()
    with job_run("compute_factors") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            days = month_ends(cur, args.backfill_months) if args.backfill_months else []
            days.append(args.as_of or latest_bar_day(cur))
            total = 0
            for d in days:
                scored = fx.score_frame(load_inputs(cur, d))
                n = store(cur, scored)
                conn.commit()  # one day at a time: a failure keeps the days already done
                el = scored[scored["eligible"]]
                cover = {f: int(el[f"z_{f}"].notna().sum()) for f in fx.FACTORS}
                em.emit("INFO", "factor_day", f"factor scores {d}",
                        payload={"as_of": str(d), "rows": n, "eligible": len(el), "coverage": cover})
                total += n
            prune(cur)
        em.emit("INFO", "rows_written", "factor_scores", payload={"rows": total, "days": len(days)})


if __name__ == "__main__":
    main()
