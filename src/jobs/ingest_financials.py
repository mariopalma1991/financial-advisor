"""Daily financials ingestion: Finnhub financials as reported.

Idempotent: raw upserts ignore duplicates on (source, ticker, access_number).

Rate limit: 60 Finnhub calls/min → 1.1s between calls.
"""

from __future__ import annotations

import time

from src.adapters.finnhub_financials import FinnhubFinancialsAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 1.1


def main() -> None:
    with job_run("ingest_financials") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        adapter = FinnhubFinancialsAdapter()
        all_reports = []
        for i, symbol in enumerate(symbols):
            try:
                reports = adapter.fetch([symbol], freq="quarterly")
                all_reports.extend(reports)
            except Exception:
                pass
            if i < len(symbols) - 1:
                time.sleep(_CALL_DELAY)

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub financials",
            payload={"reports": len(all_reports), "symbols": len(symbols)},
        )

        if all_reports:
            n = raw.insert_financial_reports(all_reports)
            em.emit(
                "INFO",
                "rows_written",
                "raw_financial_reports",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()