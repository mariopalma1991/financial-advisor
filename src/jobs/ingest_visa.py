"""Monthly visa application ingestion: Finnhub H1-B and Permanent visa data.

Idempotent: raw upserts ignore duplicates on (source, ticker, case_number).

Finnhub visa data is updated quarterly by DOL. Monthly schedule is sufficient.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.finnhub_visa import FinnhubVisaAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~40 min; stop cleanly before the 60 min workflow timeout.
_BUDGET_S = 50 * 60


def main() -> None:
    with job_run("ingest_visa") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        today = date.today()
        _from = (today - timedelta(days=365)).isoformat()
        to = today.isoformat()

        adapter = FinnhubVisaAdapter()
        # Errors are counted and reported (and a rejected API key fails the
        # job) by the shared loop. No rotation: one run covers the whole universe.
        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch([s], _from=_from, to=to, strict=True),
            em=em,
            label="finnhub visa",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
        )
        all_apps = res.items

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub visa",
            payload={"apps": len(all_apps), "symbols": len(res.processed)},
        )

        if all_apps:
            n = raw.insert_visa_applications(all_apps)
            em.emit(
                "INFO",
                "rows_written",
                "raw_visa_applications",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()