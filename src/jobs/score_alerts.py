"""Score every committee alert (committee_alerts) against VOO in MXN after 1, 3
and 6 months into alert_outcomes (src/transforms/outcomes.py). Only horizons that
have passed are written, and a written outcome is final. Runs after compute_factors.
  python -m src.jobs.score_alerts
"""
from __future__ import annotations

from collections import defaultdict

import psycopg2.extras

from src.quality.run_metadata import job_run
from src.storage.supabase_client import pg_connection
from src.transforms import factors as fx
from src.transforms.outcomes import HORIZONS, Series, outcome

BENCHMARK = "VOO"


def _iso(v) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def closes(cur, tickers: list[str], since: str) -> dict[str, Series]:
    """Split-adjusted closes (today's share basis) per ticker from `since`."""
    cur.execute("select distinct on (ticker, bar_date) ticker, bar_date, close, ingested_at from price_bars_daily "
                "where ticker = any(%s) and bar_date >= %s::date - 5 and close > 0 "
                "order by ticker, bar_date, ingested_at desc", (tickers, since))
    bars: dict[str, list[dict]] = defaultdict(list)
    for r in cur.fetchall():
        bars[r["ticker"]].append({"bar_date": _iso(r["bar_date"]), "close": float(r["close"]),
                                  "ingested_at": _iso(r["ingested_at"])})
    cur.execute("select distinct ticker, ex_date, ratio from raw_corporate_actions "
                "where action_type = 'split' and ratio > 0 and ticker = any(%s)", (tickers,))
    splits: dict[str, list[dict]] = defaultdict(list)
    for r in cur.fetchall():
        splits[r["ticker"]].append({"ex_date": _iso(r["ex_date"]), "ratio": float(r["ratio"])})
    return {t: Series(list(zip((x["bar_date"] for x in b), fx.adjust_for_splits(b, splits[t]))))
            for t, b in bars.items()}


def main() -> None:
    with job_run("score_alerts") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select a.id, a.ticker, a.alert_day, array_agg(o.horizon_days) filter "
                        "(where o.horizon_days is not null) as done from committee_alerts a "
                        "left join alert_outcomes o on o.alert_id = a.id group by a.id")
            todo = [r for r in cur.fetchall() if len(r["done"] or []) < len(HORIZONS)]
            if not todo:
                em.emit("INFO", "rows_written", "alert_outcomes", payload={"rows": 0, "open_alerts": 0})
                return
            since = min(_iso(r["alert_day"]) for r in todo)
            series = closes(cur, sorted({r["ticker"] for r in todo} | {BENCHMARK}), since)
            cur.execute("select distinct on (obs_date) obs_date, value from macro_observations "
                        "where series_id = 'DEXMXUS' and value > 0 order by obs_date, ingested_at desc")
            usd_mxn = Series([(_iso(r["obs_date"]), float(r["value"])) for r in cur.fetchall()])
            bench = series[BENCHMARK]
            rows = []
            for a in todo:
                stock = series.get(a["ticker"])
                if not stock:
                    continue
                for h in HORIZONS:
                    if h in (a["done"] or []):
                        continue
                    o = outcome(bench.days, _iso(a["alert_day"]), h, stock, bench, usd_mxn)
                    # final once written, so wait for the exit day's exchange rate (FRED lags)
                    if o and usd_mxn.days and usd_mxn.days[-1] >= o["exit_day"]:
                        rows.append((a["id"], h, o["entry_day"], o["exit_day"], o["ret_mxn"],
                                     o["bench_ret_mxn"], o["excess"]))
            if rows:
                psycopg2.extras.execute_values(
                    cur, "insert into alert_outcomes (alert_id, horizon_days, entry_day, exit_day, ret_mxn, "
                         "bench_ret_mxn, excess) values %s on conflict do nothing", rows)
            conn.commit()
            em.emit("INFO", "rows_written", "alert_outcomes", payload={"rows": len(rows), "open_alerts": len(todo)})


if __name__ == "__main__":
    main()
