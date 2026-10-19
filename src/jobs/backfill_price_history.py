"""Fetch Yahoo daily closes and volumes for the enabled tickers (plus the
benchmarks) over [--start, --end] into the cold archive (src/storage/price_history.py).

The range overlaps the database on purpose: readers use the file only before the
database's first bar, and roll_window keeps trimming that first bar forward.
  python -m src.jobs.backfill_price_history --start 2023-09-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

import psycopg2.extras

from src.adapters.yfinance_prices import YFinanceAdapter
from src.quality.run_metadata import job_run
from src.storage import price_history
from src.storage.supabase_client import pg_connection

BENCHMARKS = ("VOO", "SPY")
CHUNK = 100


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    args = ap.parse_args()
    with job_run("backfill_price_history") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select ticker from current_tickers where enabled order by ticker")
            tickers = sorted({r["ticker"] for r in cur.fetchall()} | set(BENCHMARKS))
        fetched_at = datetime.now(UTC).isoformat()
        yahoo = YFinanceAdapter()
        rows, missing = [], []
        for i in range(0, len(tickers), CHUNK):
            chunk = tickers[i:i + CHUNK]
            bars = [b for b in yahoo.fetch_bars(chunk, args.start) if b.bar_date <= args.end]
            got = {b.ticker for b in bars}
            missing += [t for t in chunk if t not in got]
            # Yahoo's closes come split-adjusted to the fetch day, so ingested_at is the
            # fetch time (adjust_for_splits leaves them as they are)
            rows += [{"ticker": b.ticker, "bar_date": b.bar_date, "close": b.close,
                      "volume": b.volume, "ingested_at": fetched_at} for b in bars]
        n = price_history.write(rows)
        em.emit("INFO", "rows_written", price_history.KEY, payload={
            "rows": n, "tickers": len(tickers) - len(missing), "missing": missing[:50],
            "start": str(args.start), "end": str(args.end)})


if __name__ == "__main__":
    main()
