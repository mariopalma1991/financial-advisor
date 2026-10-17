"""Daily filings ingestion: SEC EDGAR filing lists.

One EDGAR submissions call per company returns all its recent filings (form,
filing date, 8-K items, primary document), so the whole universe takes a few
minutes and uses no Finnhub quota. Finnhub's /stock/filings was the source
until 2026-10-17; it returned only part of EDGAR (e.g. no Form 4 or 6-K after
2026-10-12), which a comparison against EDGAR showed.

Only the forms in platform.yaml ingestion.filings.keep_forms are stored; rows of
any other form are deleted (not archived), so changing the list cleans the table
on the next run.

Idempotent: raw upserts ignore duplicates on accession_no.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from src.adapters.base import RawFiling
from src.adapters.sec_edgar import EdgarClient, parse_index_url, parse_recent_filings
from src.config import all_symbols, load_platform
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import EventEmitter, job_run
from src.storage import curated, raw
from src.storage.supabase_client import pg_connection

_BUDGET_S = 40 * 60


def known_ciks() -> dict[str, int]:
    """ticker -> CIK from filings already stored (fallback when SEC's list lacks a ticker)."""
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("select distinct on (ticker) ticker, url from raw_filings order by ticker, filed_at desc")
        out = {}
        for ticker, url in cur.fetchall():
            parsed = parse_index_url(url)
            if parsed:
                out[ticker] = parsed[0]
        return out


def resolve_ciks(symbols: list[str], sec: dict[str, int], known: dict[str, int]) -> dict[str, int]:
    out = {}
    for s in symbols:
        cik = sec.get(s.upper().replace(".", "-")) or known.get(s)
        if cik:
            out[s] = cik
    return out


def to_raw_filing(ticker: str, cik: int, f: dict) -> RawFiling:
    acc = f["accession"]
    return RawFiling(
        source="sec_edgar",
        ticker=ticker,
        accession_no=acc,
        form_type=f["form"],
        # filing day at 00:00 UTC, like the Finnhub rows before it; the exact
        # acceptance time is in the payload
        filed_at=datetime.fromisoformat(f["filing_date"]).replace(tzinfo=UTC),
        url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.html",
        payload={"cik": cik, "accepted_date": f["accepted"], "items": f["items"],
                 "primary_document": f["primary"], "report_date": f["report_date"]},
    )


def run(em: EventEmitter, since: str) -> None:
    cfg = load_platform().ingestion["filings"]
    keep = set(cfg["keep_forms"])
    synced = curated.sync_dim_tickers()
    symbols = all_symbols()
    edgar = EdgarClient()
    ciks = resolve_ciks(symbols, edgar.company_tickers(), known_ciks())
    missing = sorted(set(symbols) - set(ciks))
    em.emit("WARN" if missing else "INFO", "ciks_resolved", "ticker -> CIK",
            payload={"resolved": len(ciks), "missing": len(missing), "missing_sample": missing[:20]})

    def fetch(symbol: str) -> list[RawFiling]:
        cik = ciks[symbol]
        return [to_raw_filing(symbol, cik, f)
                for f in parse_recent_filings(edgar.submissions_json(cik), since, keep)]

    res = fetch_symbols(
        sorted(ciks),
        fetch,
        em=em,
        label="sec edgar filings",
        delay_s=0.0,  # EdgarClient paces requests itself
        max_symbols=len(ciks),
        budget_s=_BUDGET_S,
        flush=raw.insert_filings,
    )
    em.emit("INFO", "rows_written", "raw_filings",
            payload={"rows": res.rows_written, "symbols": len(res.processed), "since": since,
                     "dim_tickers_synced": synced})
    removed = raw.delete_filings_except(sorted(keep))
    if removed:
        em.emit("INFO", "rows_deleted", "raw_filings outside keep_forms", payload={"rows": removed})


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=None, help="look back N days (default: platform.yaml)")
    args = p.parse_args(argv)
    days = args.days or int(load_platform().ingestion["filings"]["lookback_days"])
    with job_run("ingest_filings") as em:
        run(em, (datetime.now(UTC).date() - timedelta(days=days)).isoformat())


if __name__ == "__main__":
    main()
