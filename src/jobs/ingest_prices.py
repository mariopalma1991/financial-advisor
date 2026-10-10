"""Daily price ingestion: yfinance -> raw -> curated.

Idempotent: raw upserts ignore duplicates; curated upserts insert only on change.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import all_symbols
from src.quality.checks import check_freshness
from src.quality.run_metadata import job_run
from src.storage import curated, raw


def main() -> None:
    with job_run("ingest_prices") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()
        since = date.today() - timedelta(days=1)  # just yesterday; backfill owns history

        yf = YFinanceAdapter()
        bars = yf.fetch_bars(symbols, since)
        em.emit("INFO", "source_fetched", "yfinance bars", payload={"rows": len(bars)})

        actions = yf.fetch_corporate_actions(symbols, since - timedelta(days=365))
        em.emit("INFO", "source_fetched", "yfinance corporate actions", payload={"rows": len(actions)})

        raw.insert_price_bars(bars)
        raw.insert_corporate_actions(actions)

        n_bars = curated.upsert_price_bars(bars)
        n_acts = curated.upsert_corporate_actions(actions)
        em.emit(
            "INFO",
            "rows_written",
            "prices consolidated",
            payload={
                "dim_tickers_synced": synced,
                "price_bars_new": n_bars,
                "corp_actions_new": n_acts,
            },
        )

        check_freshness(em)


if __name__ == "__main__":
    main()
