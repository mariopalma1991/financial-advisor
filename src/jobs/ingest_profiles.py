"""Weekly company profiles and peers (Finnhub /stock/profile2 + /stock/peers).

Profiles change rarely, so each run refreshes a third of the universe (rotating by
GITHUB_RUN_NUMBER): ~385 tickers x 2 calls x 2.1 s = ~27 min. Feeds company_profiles
and the peer_comparison view.
"""
from __future__ import annotations

from src.adapters.finnhub_company import FinnhubCompanyAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated

_CALL_DELAY = 4.2  # two Finnhub calls per symbol, 30 calls/min
_BUDGET_S = 40 * 60


def main() -> None:
    with job_run("ingest_profiles") as em:
        symbols = all_symbols()
        adapter = FinnhubCompanyAdapter()
        res = fetch_symbols(
            symbols,
            adapter.profile,
            em=em,
            label="finnhub profile + peers",
            delay_s=_CALL_DELAY,
            max_symbols=-(-len(symbols) // 3),
            budget_s=_BUDGET_S,
            flush=curated.upsert_company_profiles,
        )
        em.emit("INFO", "rows_written", "company_profiles",
                payload={"rows": res.rows_written, "symbols": len(res.processed)})


if __name__ == "__main__":
    main()
