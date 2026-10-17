"""Daily earnings ingestion: Finnhub earnings surprises.

Idempotent: raw upserts ignore duplicates on (source, ticker, period).

Finnhub free tier returns last 4 quarters. Weekly schedule is sufficient.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from src.adapters.finnhub_earnings import FinnhubEarningsAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~46 min. Rows are written every 100 symbols inside
# this budget, which leaves room for setup before the 60 min workflow timeout.
_BUDGET_S = 48 * 60


def main() -> None:
    with job_run("ingest_earnings") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        adapter = FinnhubEarningsAdapter()
        # Errors are counted and reported (and a rejected API key fails the
        # job) by the shared loop. No rotation: one run covers the whole universe.
        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch([s], limit=5, strict=True),
            em=em,
            label="finnhub earnings",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
            flush=raw.insert_earnings_surprises,
        )
        em.emit(
            "INFO",
            "source_fetched",
            "finnhub earnings",
            payload={"surprises": res.n_items, "symbols": len(res.processed)},
        )

        if res.rows_written:
            em.emit(
                "INFO",
                "rows_written",
                "raw_earnings_surprises",
                payload={"rows": res.rows_written, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()