"""Reset all market data and re-backfill 16 curated tickers.
Step 1: Delete all existing data from Supabase
Step 2: Backfill 5 years of daily prices + indicators
Step 3: Fetch news (RSS — limited to ~2 days)
Step 4: Fetch fundamentals (EDGAR — requires valid User-Agent)
"""
import sys
sys.path.insert(0, ".")

from datetime import date, timedelta

import pandas as pd

from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import all_symbols, load_platform
from src.jobs.compute_indicators import compute_and_store
from src.quality.run_metadata import job_run
from src.storage import curated, raw
from src.storage.supabase_client import get_client

YEARS = 5
TICKERS = all_symbols()


def delete_all_data() -> None:
    """Delete all rows from market data tables."""
    client = get_client()
    tables = [
        "raw_price_bars", "raw_corporate_actions", "raw_news_articles", "raw_filings",
        "price_bars_daily", "corporate_actions", "indicators_daily",
        "fundamentals_quarterly", "news_articles", "news_sentiment_daily",
    ]
    for table in tables:
        try:
            # Delete all rows where id > 0 (all rows)
            client.table(table).delete().gte("id", 0).execute()
            print(f"  Cleared {table}")
        except Exception as e:
            print(f"  {table}: {e}")


def backfill_prices() -> None:
    """Backfill YEARS of daily price data + indicators."""
    since = date.today() - timedelta(days=YEARS * 365)
    cfg = load_platform()
    bench_cache: dict[str, pd.Series] = {}
    yf = YFinanceAdapter()

    with job_run("backfill_curated") as em:
        curated.sync_dim_tickers()

        for ticker in TICKERS:
            print(f"  [{ticker}] fetching {YEARS}y of bars...")
            bars = yf.fetch_bars([ticker], since)
            actions = yf.fetch_corporate_actions([ticker], since)

            raw.insert_price_bars(bars)
            raw.insert_corporate_actions(actions)
            n_bars = curated.upsert_price_bars(bars)
            curated.upsert_corporate_actions(actions)

            # Get ticker info for benchmark
            info = next((t for t in curated.enabled_tickers() if t["ticker"] == ticker), None)
            benchmark = info["benchmark"] if info else "SPY"

            n_inds = compute_and_store(ticker, benchmark, cfg.indicators, bench_cache, full=True)
            print(f"  [{ticker}] bars={n_bars}, indicators={n_inds}")


def main() -> None:
    print(f"Curated tickers: {TICKERS}")
    print(f"Years: {YEARS}")
    print()

    print("[1/2] Deleting all existing data...")
    delete_all_data()

    print(f"\n[2/2] Backfilling {YEARS}y prices + indicators...")
    backfill_prices()

    print("\nDone. News and fundamentals need separate handling:")
    print("  - News: RSS feeds only provide ~2 days. Run: python -m src.jobs.ingest_news")
    print("  - Fundamentals: EDGAR requires valid EDGAR_USER_AGENT. Run: python -m src.jobs.ingest_filings")


if __name__ == "__main__":
    main()