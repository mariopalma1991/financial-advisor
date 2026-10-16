"""Daily financials ingestion: Finnhub financials as reported.

Idempotent: raw upserts ignore duplicates on (source, ticker, access_number).

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from src.adapters.finnhub_financials import FinnhubFinancialsAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~40 min; stop cleanly before the 60 min workflow timeout.
_BUDGET_S = 50 * 60


def main() -> None:
    with job_run("ingest_financials") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        adapter = FinnhubFinancialsAdapter()
        # Errors are counted and reported (and a rejected API key fails the
        # job) by the shared loop. No rotation: one run covers the whole universe.
        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch([s], freq="quarterly", strict=True),
            em=em,
            label="finnhub financials",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
        )
        all_reports = res.items

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub financials",
            payload={"reports": len(all_reports), "symbols": len(res.processed)},
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