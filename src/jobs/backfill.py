"""Historical backfill for tickers on first add or promotion (screening -> analysis).

Usage: python -m src.jobs.backfill AAPL MSFT WALMEX.MX
Depth comes from config/platform.yaml (3y analysis tier, 1y screening tier).
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd

from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import load_platform, tier_of
from src.jobs.compute_indicators import compute_and_store
from src.quality.run_metadata import job_run
from src.storage import curated, raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill price history + indicators")
    parser.add_argument("tickers", nargs="+", help="symbols to backfill")
    args = parser.parse_args()

    cfg = load_platform()
    prices_cfg = cfg.ingestion["prices"]
    bench_cache: dict[str, pd.Series] = {}

    with job_run("backfill") as em:
        curated.sync_dim_tickers()  # dim_ticker must reflect config before benchmark lookup
        for ticker in args.tickers:
            years_key = (
                "backfill_years_analysis"
                if tier_of(ticker) == "analysis"
                else "backfill_years_screening"
            )
            since = date.today() - timedelta(days=int(365.25 * float(prices_cfg[years_key])))

            yf = YFinanceAdapter()
            bars = yf.fetch_bars([ticker], since)
            actions = yf.fetch_corporate_actions([ticker], since)
            raw.insert_price_bars(bars)
            raw.insert_corporate_actions(actions)
            n_bars = curated.upsert_price_bars(bars)
            n_acts = curated.upsert_corporate_actions(actions)

            info = next((t for t in curated.enabled_tickers() if t["ticker"] == ticker), None)
            n_inds = 0
            if info:
                n_inds = compute_and_store(
                    ticker, info["benchmark"], cfg.indicators, bench_cache, full=True
                )

            em.emit(
                "INFO",
                "rows_written",
                f"backfilled {ticker}",
                ticker=ticker,
                payload={
                    "since": since.isoformat(),
                    "bars": n_bars,
                    "actions": n_acts,
                    "indicators": n_inds,
                },
            )


if __name__ == "__main__":
    main()
