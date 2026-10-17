"""Weekly earnings calendar ingestion: Finnhub earnings calendar.

Idempotent: raw upserts ignore duplicates on (source, ticker, event_date, quarter).

Finnhub free tier returns 1 month of data. Weekly schedule keeps the window fresh.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.finnhub_earnings_calendar import FinnhubEarningsCalendarAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~46 min. Rows are written every 100 symbols inside
# this budget, which leaves room for setup before the 60 min workflow timeout.
_BUDGET_S = 48 * 60


def main() -> None:
    with job_run("ingest_earncal") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        today = date.today()
        _from = (today - timedelta(days=7)).isoformat()
        to = (today + timedelta(days=30)).isoformat()

        adapter = FinnhubEarningsCalendarAdapter()
        # Errors are counted and reported (and a rejected API key fails the
        # job) by the shared loop. No rotation: one run covers the whole universe.
        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch(_from=_from, to=to, symbols=[s], strict=True),
            em=em,
            label="finnhub earnings calendar",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
            flush=raw.insert_earnings_calendar,
        )
        em.emit(
            "INFO",
            "source_fetched",
            "finnhub earnings calendar",
            payload={"events": res.n_items, "symbols": len(res.processed)},
        )

        if res.rows_written:
            em.emit(
                "INFO",
                "rows_written",
                "raw_earnings_calendar",
                payload={"rows": res.rows_written, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()