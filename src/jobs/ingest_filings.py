"""Daily filings ingestion: Finnhub SEC filing metadata (10-K, 10-Q, 8-K).

Idempotent: raw upserts ignore duplicates on accession_no.

Rate limit: 60 Finnhub calls/min, one call per symbol. The per-symbol loop is
time-budgeted and rotates through the universe (see src/jobs/_symbol_loop.py);
limits live in config/platform.yaml -> ingestion.filings.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.finnhub_filings import FinnhubFilingsAdapter
from src.config import all_symbols, load_platform
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import EventEmitter, job_run
from src.storage import curated, raw


def run(
    em: EventEmitter,
    _from: str,
    *,
    max_symbols: int | None = None,
    budget_s: float,
) -> None:
    """Fetch filings filed on/after `_from` and write them to raw_filings."""
    cfg = load_platform().ingestion["filings"]
    synced = curated.sync_dim_tickers()
    symbols = all_symbols()

    to = date.today().isoformat()
    adapter = FinnhubFilingsAdapter()

    res = fetch_symbols(
        symbols,
        lambda s: adapter.fetch_filings([s], _from=_from, to=to, strict=True),
        em=em,
        label="finnhub filings",
        delay_s=float(cfg["call_delay_s"]),
        max_symbols=max_symbols or len(symbols),
        budget_s=budget_s,
    )

    em.emit(
        "INFO",
        "source_fetched",
        "finnhub filings",
        payload={"filings": len(res.items), "symbols": len(res.processed)},
    )

    if res.items:
        n = raw.insert_filings(res.items)
        em.emit(
            "INFO",
            "rows_written",
            "raw_filings",
            payload={"rows": n, "dim_tickers_synced": synced},
        )


def main() -> None:
    cfg = load_platform().ingestion["filings"]
    _from = (date.today() - timedelta(days=int(cfg["lookback_days"]))).isoformat()
    with job_run("ingest_filings") as em:
        run(
            em,
            _from,
            max_symbols=int(cfg["max_symbols_per_run"]),
            budget_s=float(cfg["fetch_budget_min"]) * 60,
        )


if __name__ == "__main__":
    main()
