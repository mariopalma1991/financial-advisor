"""Check Finnhub SEC filings coverage for screening universe tickers.

Finnhub's /stock/filings endpoint returns SEC filings (8-K, 10-K, 10-Q, etc.)
for US-listed companies. Tests each NA ticker with a recent date window.

Rate limit: 30 calls/min → 2.1s between calls.
Progress is saved incrementally.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import finnhub
from dotenv import load_dotenv

from src.config import load_screening_universe

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# North American exchanges that file with SEC
NA_EXCHANGES: set[str] = {
    "NYSE",
    "NASDAQ",
    "TORONTO STOCK EXCHANGE",
    "TSE",
    "OVER THE COUNTER US",
}

CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
OUTPUT_FILE = Path(__file__).resolve().parent / "finnhub_sec_coverage.json"
PROGRESS_FILE = Path(__file__).resolve().parent / "finnhub_sec_coverage_progress.json"


def load_progress() -> dict[str, bool | str]:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(results: dict[str, bool | str]) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def main() -> None:
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not set in .env")

    client = finnhub.Client(api_key=api_key)
    universe = load_screening_universe()

    na_tickers = [t for t in universe.enabled() if t.exchange in NA_EXCHANGES]
    print(f"NA tickers: {len(na_tickers)}")
    print(f"Estimated time: {len(na_tickers) * CALL_DELAY / 60:.1f} minutes")

    # Load progress
    progress = load_progress()
    done = {k for k, v in progress.items() if isinstance(v, bool)}
    pending = [t for t in na_tickers if t.symbol not in done]

    if done:
        print(f"Resuming: {len(done)} checked, {len(pending)} remaining")

    _from = "2026-09-01"
    to = date.today().isoformat()

    print(f"\nChecking SEC filings from {_from} to {to}...")
    print(f"{'TICKER':<10} {'EXCHANGE':<25} {'FILINGS':<10} STATUS")
    print("-" * 60)

    start_time = time.time()
    calls_made = 0

    for i, ticker in enumerate(pending):
        symbol = ticker.symbol
        exchange = ticker.exchange

        try:
            filings = client.filings(symbol=symbol, _from=_from, to=to)
            count = len(filings)

            if count > 0:
                progress[symbol] = True
                if i < 30 or count > 10:
                    print(f"{symbol:<10} {exchange:<25} {count:<10} OK")
            else:
                progress[symbol] = False
                if i < 20:
                    print(f"{symbol:<10} {exchange:<25} {count:<10} NONE")

        except Exception as e:
            err_msg = str(e)[:80]
            progress[symbol] = err_msg
            print(f"{symbol:<10} {exchange:<25} {'-':<10} ERROR: {err_msg}")

        calls_made += 1

        if calls_made % 50 == 0:
            save_progress(progress)
            elapsed = time.time() - start_time
            rate = calls_made / (elapsed / 60)
            remaining = len(pending) - (i + 1)
            eta_min = (remaining * CALL_DELAY) / 60
            tw = sum(1 for v in progress.values() if v is True)
            print(f"\n--- Progress: {i + 1 + len(done)}/{len(na_tickers)} checked, "
                  f"{rate:.0f} calls/min, {tw} with filings, ETA: {eta_min:.1f} min ---\n")

        time.sleep(CALL_DELAY)

    # Final save
    save_progress(progress)

    with_filings = {k for k, v in progress.items() if v is True}
    without_filings = {k for k, v in progress.items() if v is False}
    errors = {k: v for k, v in progress.items() if isinstance(v, str)}

    result = {
        "with_filings": sorted(with_filings),
        "without_filings": sorted(without_filings),
        "errors": errors,
        "total_na_tickers": len(na_tickers),
        "with_filings_count": len(with_filings),
        "without_filings_count": len(without_filings),
        "error_count": len(errors),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"SEC FILINGS COVERAGE:")
    print(f"  NA tickers:           {len(na_tickers)}")
    print(f"  With SEC filings:     {len(with_filings)} ({100*len(with_filings)/len(na_tickers):.1f}%)")
    print(f"  Without SEC filings:  {len(without_filings)} ({100*len(without_filings)/len(na_tickers):.1f}%)")
    print(f"  Errors:               {len(errors)}")
    print(f"\nSaved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()