"""Weekly earnings calendar ingestion: Finnhub earnings calendar.

Idempotent: raw upserts ignore duplicates on (source, ticker, event_date, quarter).

Finnhub free tier returns 1 month of data. Weekly schedule keeps the window fresh.

Rate limit: 60 Finnhub calls/min → 1.1s between calls.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from src.adapters.finnhub_earnings_calendar import FinnhubEarningsCalendarAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 1.1


def main() -> None:
    with job_run("ingest_earncal") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        today = date.today()
        _from = (today - timedelta(days=7)).isoformat()
        to = (today + timedelta(days=30)).isoformat()

        adapter = FinnhubEarningsCalendarAdapter()
        all_events = []
        for i, symbol in enumerate(symbols):
            try:
                events = adapter.fetch(_from=_from, to=to, symbols=[symbol])
                all_events.extend(events)
            except Exception:
                pass
            if i < len(symbols) - 1:
                time.sleep(_CALL_DELAY)

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub earnings calendar",
            payload={"events": len(all_events), "symbols": len(symbols)},
        )

        if all_events:
            n = raw.insert_earnings_calendar(all_events)
            em.emit(
                "INFO",
                "rows_written",
                "raw_earnings_calendar",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()