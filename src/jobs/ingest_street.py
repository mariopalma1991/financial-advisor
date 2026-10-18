"""Weekly Yahoo street data: EPS estimate trends and revisions, analyst price
targets, short interest and analyst rating changes (adapters/yahoo_street.py).

Writes estimate_trends, street_snapshot and analyst_rating_changes; agents read
them through the estimate_revisions and street_latest views (db/migrations/0014).
One request per ticker, spaced 1.2 s: ~1,150 tickers in ~25 min. Yahoo is not
Finnhub, so this runs outside the finnhub-api concurrency group.
"""
from __future__ import annotations

import os

from src.adapters.yahoo_street import RATING_LOOKBACK_DAYS, YahooStreetAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated

_CALL_DELAY = 1.2
_BUDGET_S = 50 * 60
_KEEP_WEEKS = 26  # estimate trends already carry 7/30/60/90-day history


def main() -> None:
    with job_run("ingest_street") as em:
        symbols = all_symbols()
        adapter = YahooStreetAdapter()
        res = fetch_symbols(
            symbols,
            adapter.fetch,
            em=em,
            label="yahoo street data",
            delay_s=_CALL_DELAY,
            max_symbols=int(os.getenv("MAX_SYMBOLS") or len(symbols)),  # smaller for test runs
            budget_s=_BUDGET_S,
            flush=curated.upsert_street,
        )
        curated.prune_street(keep_weeks=_KEEP_WEEKS, rating_days=RATING_LOOKBACK_DAYS)
        em.emit("INFO", "rows_written", "estimate_trends, street_snapshot, analyst_rating_changes",
                payload={"rows": res.rows_written, "symbols": len(res.processed)})


if __name__ == "__main__":
    main()
