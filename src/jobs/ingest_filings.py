"""Daily filings ingestion: Finnhub SEC filing metadata (10-K, 10-Q, 8-K).

Idempotent: raw upserts ignore duplicates on accession_no.

Rate limit: 60 Finnhub calls/min → 1.1s between calls.
"""

from __future__ import annotations

import time

from src.adapters.finnhub_filings import FinnhubFilingsAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 1.1


def main() -> None:
    with job_run("ingest_filings") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        adapter = FinnhubFilingsAdapter()
        all_filings = []
        for i, symbol in enumerate(symbols):
            try:
                filings = adapter.fetch_filings([symbol])
                all_filings.extend(filings)
            except Exception:
                pass
            if i < len(symbols) - 1:
                time.sleep(_CALL_DELAY)

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub filings",
            payload={"filings": len(all_filings), "symbols": len(symbols)},
        )

        if all_filings:
            n = raw.insert_filings(all_filings)
            em.emit(
                "INFO",
                "rows_written",
                "raw_filings",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()
