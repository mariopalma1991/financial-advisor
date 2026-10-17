"""Daily price ingestion: yfinance -> raw -> curated.

Idempotent: raw upserts ignore duplicates; curated upserts insert only on change.
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import all_symbols, benchmark_symbols
from src.quality.checks import check_freshness
from src.quality.run_metadata import EventEmitter, job_run
from src.storage import curated, raw


def run(em: EventEmitter, since: date, only_benchmarks: bool = False) -> None:
    """Fetch bars on/after `since` for the universe plus its benchmark ETFs.
    Idempotent: bars already stored unchanged are skipped."""
    synced = curated.sync_dim_tickers()
    symbols = benchmark_symbols() if only_benchmarks else all_symbols() + benchmark_symbols()

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily price ingestion")
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("LOOKBACK_DAYS") or 1),
        help="days to look back (default 1 = just yesterday; raise it to fill gaps "
        "after failed runs — idempotent, existing bars are skipped)",
    )
    parser.add_argument(
        "--only-benchmarks",
        action="store_true",
        default=os.getenv("ONLY_BENCHMARKS") == "true",
        help="fetch only the benchmark ETFs (to backfill them without the whole universe)",
    )
    args = parser.parse_args()

    with job_run("ingest_prices") as em:
        run(em, date.today() - timedelta(days=args.days), only_benchmarks=args.only_benchmarks)


if __name__ == "__main__":
    main()
