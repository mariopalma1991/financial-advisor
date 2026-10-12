"""Monthly visa application ingestion: Finnhub H1-B and Permanent visa data.

Idempotent: raw upserts ignore duplicates on (source, ticker, case_number).

Finnhub visa data is updated quarterly by DOL. Monthly schedule is sufficient.

Rate limit: 60 Finnhub calls/min → 1.1s between calls.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from src.adapters.finnhub_visa import FinnhubVisaAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 1.1


def main() -> None:
    with job_run("ingest_visa") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        today = date.today()
        _from = (today - timedelta(days=365)).isoformat()
        to = today.isoformat()

        adapter = FinnhubVisaAdapter()
        all_apps = []
        for i, symbol in enumerate(symbols):
            try:
                apps = adapter.fetch([symbol], _from=_from, to=to)
                all_apps.extend(apps)
            except Exception:
                pass
            if i < len(symbols) - 1:
                time.sleep(_CALL_DELAY)

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub visa",
            payload={"apps": len(all_apps), "symbols": len(symbols)},
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