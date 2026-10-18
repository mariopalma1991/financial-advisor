"""Open-market insider purchases (Form 4, code P) from SEC's quarterly Insider
Transactions Data Sets into insider_purchases, so the insider-buying factor has
history from before our own Form 4 loads (insider_trades starts 2026-07-20).
compute_factors reads these rows only for days before insider_trades begins.
One zip per quarter (about 50-100 MB); run from GitHub Actions (backfill_insider_purchases.yml):
  python -m src.jobs.backfill_insider_purchases --quarters 2024q3 2024q4 2025q1
A quarter SEC hasn't published yet is skipped. Reruns replace that quarter's rows.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import httpx
import psycopg2.extras

from src.adapters.sec_edgar import EdgarClient
from src.adapters.sec_insider_datasets import URL, parse_purchases
from src.config import all_symbols
from src.jobs.ingest_filings import known_ciks, resolve_ciks
from src.quality.run_metadata import current_run_id, job_run
from src.storage.supabase_client import pg_connection

_COLS = ("accession", "ticker", "filed_at", "first_trade_date", "owner", "role", "is_10b5_1", "buy_value",
         "quarter", "run_id")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quarters", nargs="+", required=True, help="e.g. 2024q3 2024q4")
    args = ap.parse_args(argv)
    with job_run("backfill_insider_purchases") as em:
        edgar = EdgarClient()
        ciks = resolve_ciks(all_symbols(), edgar.company_tickers(), known_ciks())
        by_cik: dict[int, list[str]] = defaultdict(list)
        for t, c in ciks.items():
            by_cik[c].append(t)
        run_id = current_run_id()
        for q in args.quarters:
            q = q.lower()
            try:
                body = edgar._get(URL.format(q=q)).content
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    em.emit("WARN", "quarter_missing", q, payload={"url": URL.format(q=q)})
                    continue
                raise
            rows = parse_purchases(body, dict(by_cik))
            with pg_connection() as conn, conn.cursor() as cur:
                cur.execute("delete from insider_purchases where quarter = %s", (q,))
                if rows:
                    psycopg2.extras.execute_values(
                        cur, f"insert into insider_purchases ({', '.join(_COLS)}) values %s "
                             "on conflict (accession, ticker) do nothing",
                        [tuple({**r, "quarter": q, "run_id": run_id}.get(c) for c in _COLS) for r in rows],
                        page_size=500)
                conn.commit()
            em.emit("INFO", "rows_written", f"insider_purchases {q}", payload={
                "rows": len(rows), "tickers": len({r["ticker"] for r in rows}), "mb": round(len(body) / 1e6, 1),
                "with_10b5_1_flag": sum(r["is_10b5_1"] is not None for r in rows)})


if __name__ == "__main__":
    main()
