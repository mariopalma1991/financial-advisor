"""Weekly fundamentals ingestion: Finnhub basic financials (/stock/metric).

Writes fundamentals_snapshot (one row per ticker per run: valuation, margins,
growth, leverage) and fundamentals_quarterly (last 12 quarters, versioned: only
new or revised quarters are inserted). See db/migrations/0007_fundamentals.sql.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from datetime import date

from src.adapters.finnhub_basic_financials import SNAPSHOT_METRICS, FinnhubBasicFinancialsAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~40 min. Rows are written every 100 symbols inside
# this budget, which leaves room for setup before the 60 min workflow timeout.
_BUDGET_S = 48 * 60


def write(items, as_of: date | None = None) -> int:
    """Flush a batch of BasicFinancials; returns snapshot rows written."""
    as_of = as_of or date.today()
    snapshots = [{"ticker": i.ticker, "as_of": as_of.isoformat(), **i.snapshot} for i in items]
    quarters = [{"ticker": i.ticker, **q} for i in items for q in i.quarters]
    curated.upsert_fundamentals(quarters)
    return curated.upsert_fundamentals_snapshot(snapshots)


def coverage(items) -> dict[str, float]:
    """Share of tickers with a value per snapshot column (spots renamed Finnhub keys)."""
    n = len(items) or 1
    return {c: round(sum(i.snapshot.get(c) is not None for i in items) / n, 3) for c in SNAPSHOT_METRICS}


def main() -> None:
    with job_run("ingest_fundamentals") as em:
        symbols = all_symbols()
        adapter = FinnhubBasicFinancialsAdapter()
        seen = []

        def flush(items) -> int:
            seen.extend(items)
            return write(items)

        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch([s], strict=True),
            em=em,
            label="finnhub basic financials",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
            flush=flush,
        )
        em.emit(
            "INFO",
            "source_fetched",
            "finnhub basic financials",
            payload={"tickers": res.n_items, "symbols": len(res.processed), "coverage": coverage(seen)},
        )
        if res.rows_written:
            em.emit("INFO", "rows_written", "fundamentals_snapshot", payload={"rows": res.rows_written})


if __name__ == "__main__":
    main()
