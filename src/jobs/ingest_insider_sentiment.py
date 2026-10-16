"""Weekly insider sentiment ingestion: Finnhub monthly MSPR + net share change.

Idempotent: a month is only re-written when its values changed (see
raw.insert_insider_sentiment), so the still-open current month keeps a history
of revisions instead of being overwritten.

Data is monthly and built from Form 4 filings (US companies only); a weekly run
keeps the current month fresh. Each call returns the whole window, so the last
24 months are re-fetched every run at no extra API cost.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.finnhub_insider_sentiment import FinnhubInsiderSentimentAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~40 min; stop cleanly before the 60 min workflow timeout.
_BUDGET_S = 50 * 60
_WINDOW_DAYS = 730


def main() -> None:
    with job_run("ingest_insider_sentiment") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        today = date.today()
        _from = (today - timedelta(days=_WINDOW_DAYS)).isoformat()
        to = today.isoformat()

        adapter = FinnhubInsiderSentimentAdapter()
        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch([s], _from=_from, to=to, strict=True),
            em=em,
            label="finnhub insider sentiment",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
        )
        rows = res.items

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub insider sentiment",
            payload={"months": len(rows), "symbols": len(res.processed)},
        )

        if rows:
            n = raw.insert_insider_sentiment(rows)
            em.emit(
                "INFO",
                "rows_written",
                "raw_insider_sentiment",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()
