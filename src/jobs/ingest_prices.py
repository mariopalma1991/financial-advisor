"""Daily price ingestion: yfinance (primary) + Stooq (cross-check) -> raw -> curated.

Idempotent: raw upserts ignore duplicates; curated upserts insert only on change.
"""
from __future__ import annotations

from datetime import date, timedelta

from src.adapters.stooq_prices import StooqAdapter
from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import all_symbols
from src.quality.checks import check_freshness, compare_closes
from src.quality.run_metadata import job_run
from src.storage import curated, raw


def main() -> None:
    with job_run("ingest_prices") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()
        since = date.today() - timedelta(days=30)  # rolling window; backfill owns history

        yf = YFinanceAdapter()
        bars = yf.fetch_bars(symbols, since)
        em.emit("INFO", "source_fetched", "yfinance bars", payload={"rows": len(bars)})

        actions = yf.fetch_corporate_actions(symbols, since - timedelta(days=365))
        em.emit("INFO", "source_fetched", "yfinance corporate actions", payload={"rows": len(actions)})

        cross = StooqAdapter().fetch_bars(symbols, since)
        em.emit("INFO", "source_fetched", "stooq bars", payload={"rows": len(cross)})

        raw.insert_price_bars(bars + cross)
        raw.insert_corporate_actions(actions)

        divergences = compare_closes(bars, cross, em)
        n_bars = curated.upsert_price_bars(bars)  # primary source wins
        n_acts = curated.upsert_corporate_actions(actions)
        em.emit(
            "INFO",
            "rows_written",
            "prices consolidated",
            payload={
                "dim_tickers_synced": synced,
                "price_bars_new": n_bars,
                "corp_actions_new": n_acts,
                "divergences": len(divergences),
            },
        )

        check_freshness(em)


if __name__ == "__main__":
    main()
