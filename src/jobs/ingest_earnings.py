"""Daily earnings ingestion: Finnhub earnings surprises.

Idempotent: raw upserts ignore duplicates on (source, ticker, period).

Finnhub free tier returns last 4 quarters. Weekly schedule is sufficient.

Rate limit: 60 Finnhub calls/min → 1.1s between calls.
"""

from __future__ import annotations

import time

from src.adapters.finnhub_earnings import FinnhubEarningsAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 1.1


def main() -> None:
    with job_run("ingest_earnings") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        adapter = FinnhubEarningsAdapter()
        all_surprises = []
        for i, symbol in enumerate(symbols):
            try:
                surprises = adapter.fetch([symbol], limit=5)
                all_surprises.extend(surprises)
            except Exception:
                pass
            if i < len(symbols) - 1:
                time.sleep(_CALL_DELAY)

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub earnings",
            payload={"surprises": len(all_surprises), "symbols": len(symbols)},
        )

        if all_surprises:
            n = raw.insert_earnings_surprises(all_surprises)
            em.emit(
                "INFO",
                "rows_written",
                "raw_earnings_surprises",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()