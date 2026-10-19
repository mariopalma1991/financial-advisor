"""FINRA short interest for the tracked universe into finra_short_interest
(src/adapters/finra.py). Each run loads every settlement date not stored yet,
plus the latest two again (FINRA revises late reports). Weekly; the first run
loads two years.
  python -m src.jobs.ingest_finra_short [--since 2024-10-01]
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import psycopg2.extras

from src.adapters.finra import FinraClient, to_row
from src.config import all_symbols
from src.quality.run_metadata import current_run_id, job_run
from src.storage.supabase_client import pg_connection

HISTORY_DAYS = 730
_COLS = ("ticker", "settlement_date", "short_shares", "prior_short_shares", "change_pct", "avg_daily_volume",
         "days_to_cover", "market", "run_id")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=(datetime.now(UTC).date() - timedelta(days=HISTORY_DAYS)).isoformat())
    args = ap.parse_args(argv)
    universe = set(all_symbols())
    with job_run("ingest_finra_short") as em, pg_connection() as conn, conn.cursor() as cur:
        cur.execute("select settlement_date from finra_short_interest group by 1 order by 1")
        stored = [d.isoformat() for (d,) in cur.fetchall()]
        client = FinraClient()
        dates = client.settlement_dates(args.since)
        todo = sorted({d for d in dates if d not in stored} | set(dates[-2:]))
        run_id, total = current_run_id(), 0
        for d in todo:
            rows = [r for r in map(to_row, client.for_date(d)) if r["ticker"] in universe]
            if rows:
                psycopg2.extras.execute_values(
                    cur, f"insert into finra_short_interest ({', '.join(_COLS)}) values %s "
                         "on conflict (ticker, settlement_date) do update set "
                         + ", ".join(f"{c} = excluded.{c}" for c in _COLS[2:]) + ", ingested_at = now()",
                    [tuple({**r, "run_id": run_id}[c] for c in _COLS) for r in rows], page_size=1000)
                conn.commit()
            total += len(rows)
            em.emit("INFO", "rows_written", f"finra_short_interest {d}", payload={"rows": len(rows)})
        em.emit("INFO", "rows_written", "finra_short_interest",
                payload={"dates": len(todo), "rows": total, "latest": dates[-1] if dates else None})


if __name__ == "__main__":
    main()
