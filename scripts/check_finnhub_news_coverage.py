"""Check Finnhub news coverage for screening universe tickers.

Finnhub's /company-news endpoint only supports North American companies.
We filter to NYSE, NASDAQ, and Toronto exchanges first, then test each
ticker with a 1-week window. Rate limit: 60 calls/min → 1.1s between calls.

Progress is saved incrementally so a crash doesn't lose all work.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.finnhub_news import FinnhubNewsAdapter
from src.config import load_screening_universe

# North American exchanges Finnhub supports
NA_EXCHANGES: set[str] = {
    "NYSE",
    "NASDAQ",
    "TORONTO STOCK EXCHANGE",
    "TSE",  # Toronto Stock Exchange (TSX via legacy code)
    "OVER THE COUNTER US",
}

# Rate limit: 60 calls/min → ~1.1s between calls for safety margin
CALL_DELAY = 1.1

OUTPUT_FILE = Path(__file__).resolve().parent / "finnhub_news_coverage.json"
PROGRESS_FILE = Path(__file__).resolve().parent / "finnhub_news_coverage_progress.json"


def load_progress() -> dict[str, bool | str]:
    """Load previously saved progress."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(results: dict[str, bool | str]) -> None:
    """Save progress incrementally."""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def main() -> None:
    universe = load_screening_universe()

    # Filter to NA tickers
    na_tickers = [
        t
        for t in universe.enabled()
        if t.exchange in NA_EXCHANGES
    ]
    print(f"Screening universe: {len(universe.symbols)} total")
    print(f"North American tickers: {len(na_tickers)}")
    print(f"Estimated time: {len(na_tickers) * CALL_DELAY / 60:.1f} minutes")
    print(f"Rate limit: {60 / CALL_DELAY:.0f} calls/min (safety margin under 60)")
    print()

    # Load progress
    progress = load_progress()
    done = {k for k, v in progress.items() if isinstance(v, bool)}
    pending = [t for t in na_tickers if t.symbol not in done]

    if done:
        print(f"Resuming: {len(done)} already checked, {len(pending)} remaining")
        with_news = sum(1 for v in progress.values() if v is True)
        without_news = sum(1 for v in progress.values() if v is False)
        errors = sum(1 for v in progress.values() if isinstance(v, str))
        print(f"  So far: {with_news} with news, {without_news} without, {errors} errors")

    print(f"\nStarting coverage check...")
    print(f"{'TICKER':<10} {'EXCHANGE':<25} {'ARTICLES':<10} {'STATUS'}")
    print("-" * 60)

    start_time = time.time()
    calls_made = 0

    for i, ticker in enumerate(pending):
        symbol = ticker.symbol
        exchange = ticker.exchange

        try:
            adapter = FinnhubNewsAdapter()
            _from = (date.today() - timedelta(days=7)).isoformat()
            to = date.today().isoformat()

            articles = adapter.fetch(symbol, _from=_from, to=to)

            if articles:
                progress[symbol] = True
                status = f"OK ({len(articles)})"
            else:
                progress[symbol] = False
                status = "NO NEWS"

            print(f"{symbol:<10} {exchange:<25} {len(articles):<10} {status}")

        except Exception as e:
            err_msg = str(e)[:80]
            progress[symbol] = err_msg
            print(f"{symbol:<10} {exchange:<25} {'-':<10} ERROR: {err_msg}")

        calls_made += 1

        # Save progress every 50 calls
        if calls_made % 50 == 0:
            save_progress(progress)
            elapsed = time.time() - start_time
            rate = calls_made / (elapsed / 60)
            remaining = len(pending) - (i + 1)
            eta_min = (remaining * CALL_DELAY) / 60
            print(f"\n--- Progress: {i + 1 + len(done)}/{len(na_tickers)} checked, "
                  f"{rate:.0f} calls/min, ETA: {eta_min:.1f} min ---\n")

        # Rate limit
        time.sleep(CALL_DELAY)

    # Final save
    save_progress(progress)

    # Summarize
    with_news = {k for k, v in progress.items() if v is True}
    without_news = {k for k, v in progress.items() if v is False}
    errors = {k: v for k, v in progress.items() if isinstance(v, str)}

    result = {
        "with_news": sorted(with_news),
        "without_news": sorted(without_news),
        "errors": errors,
        "total_na_tickers": len(na_tickers),
        "with_news_count": len(with_news),
        "without_news_count": len(without_news),
        "error_count": len(errors),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"RESULTS:")
    print(f"  North American tickers tested: {len(na_tickers)}")
    print(f"  With Finhub news: {len(with_news)} ({100*len(with_news)/len(na_tickers):.1f}%)")
    print(f"  Without news:     {len(without_news)} ({100*len(without_news)/len(na_tickers):.1f}%)")
    print(f"  Errors:          {len(errors)}")
    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()