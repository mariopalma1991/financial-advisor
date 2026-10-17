"""Weekly analyst recommendation trends (Finnhub /stock/recommendation).

Writes analyst_recommendations (counts per rating per month); the analyst_consensus
view turns them into a 1-5 score and its 3-month change. One call per ticker,
~1,150 x 2.1 s = ~40 min, inside the finnhub-api concurrency group.
"""
from __future__ import annotations

from src.adapters.finnhub_company import FinnhubCompanyAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
_BUDGET_S = 48 * 60


def main() -> None:
    with job_run("ingest_analysts") as em:
        symbols = all_symbols()
        adapter = FinnhubCompanyAdapter()
        res = fetch_symbols(
            symbols,
            adapter.recommendations,
            em=em,
            label="finnhub recommendation trends",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
            flush=curated.upsert_analyst_recommendations,
        )
        em.emit("INFO", "rows_written", "analyst_recommendations",
                payload={"rows": res.rows_written, "symbols": len(res.processed)})


if __name__ == "__main__":
    main()
