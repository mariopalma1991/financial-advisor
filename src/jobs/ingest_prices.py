"""Daily price ingestion: yfinance -> raw -> curated.

Idempotent: raw upserts ignore duplicates; curated upserts insert only on change.
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import all_symbols
from src.quality.checks import check_freshness
from src.quality.run_metadata import job_run
from src.storage import curated, raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily price ingestion")
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("LOOKBACK_DAYS") or 1),
        help="days to look back (default 1 = just yesterday; raise it to fill gaps "
        "after failed runs — idempotent, existing bars are skipped)",
    )
    args = parser.parse_args()

    with job_run("ingest_prices") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()
        since = date.today() - timedelta(days=args.days)

        yf = YFinanceAdapter()
        bars = yf.fetch_bars(symbols, since)
        em.emit("INFO", "source_fetched", "yfinance bars", payload={"rows": len(bars)})

        actions = yf.fetch_corporate_actions(symbols, since - timedelta(days=365))
        em.emit("INFO", "source_fetched", "yfinance corporate actions", payload={"rows": len(actions)})

        raw.insert_price_bars(bars)
        raw.insert_corporate_actions(actions)

        n_bars = curated.upsert_price_bars(bars)
        em.emit(
            "INFO",
            "rows_written",
            "prices consolidated",
            payload={
                "dim_tickers_synced": synced,
                "price_bars_new": n_bars,
                "corp_actions_new": len(actions),
            },
        )

        check_freshness(em)


if __name__ == "__main__":
    main()
