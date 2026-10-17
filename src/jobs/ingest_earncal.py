"""Weekly earnings calendar ingestion: Finnhub earnings calendar.

Idempotent: raw upserts ignore duplicates on (source, ticker, event_date, quarter).

One call without a symbol returns every company reporting in the date range, so
we fetch the whole market one day at a time (a busy day has a few hundred
reports) and keep our universe: ~45 calls instead of one per symbol (~46 min).

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
_BUDGET_S = 20 * 60
_DAYS_BACK, _DAYS_AHEAD = 7, 37  # last week's actuals + the next five weeks


def main() -> None:
    with job_run("ingest_earncal") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        universe = set(symbols)
        today = date.today()
        days = [(today + timedelta(days=d)).isoformat() for d in range(-_DAYS_BACK, _DAYS_AHEAD + 1)]

        adapter = FinnhubEarningsCalendarAdapter()
        # The shared loop paces calls, reports errors and fails on a rejected key;
        # here each "symbol" is a day.
        res = fetch_symbols(
            days,
            lambda d: [e for e in adapter.fetch(_from=d, to=d, strict=True) if e.ticker in universe],
            em=em,
            label="finnhub earnings calendar",
            delay_s=_CALL_DELAY,
            max_symbols=len(days),
            budget_s=_BUDGET_S,
            flush=raw.insert_earnings_calendar,
        )
        em.emit(
            "INFO",
            "source_fetched",
            "finnhub earnings calendar",
            payload={"events": res.n_items, "days": len(res.processed)},
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