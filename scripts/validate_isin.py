"""Validate tickers from infoDownload (1).xls using ISIN via yfinance.
- Uses ISIN (not BMV ticker) for reliable yfinance lookups
- Rate-limit aware: delays, exponential backoff, incremental progress saves
- Resumable: skips already-tested ISINs
"""
import json
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

# ── config ──────────────────────────────────────────────────
DAYS = 5                     # fetch this many days of history
DELAY = 1.2                  # seconds between requests (avoid rate limits)
BACKOFF_BASE = 5.0           # base seconds for exponential backoff
MAX_BACKOFF = 120.0          # max backoff seconds
PROGRESS_FILE = "scripts/isin_validation_progress.json"
RESULTS_FILE = "scripts/isin_validation_results.json"
# ────────────────────────────────────────────────────────────


def load_progress() -> dict:
    """Load previously tested ISINs so we can resume."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tested": {}, "last_index": 0}


def save_progress(progress: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def test_isin(isin: str, symbol: str) -> tuple[bool, str]:
    """Test if an ISIN returns data via yfinance. Returns (ok, error_msg)."""
    try:
        t = yf.Ticker(isin)
        end = date.today()
        start = end - timedelta(days=DAYS)
        df = t.history(start=start.isoformat(), end=end.isoformat())
        if not df.empty and "Close" in df.columns and df["Close"].notna().any():
            return True, ""
        return False, "empty or no close data"
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:120]}"
        return False, msg


def main() -> None:
    # 1. Load Excel
    print("Loading infoDownload (1).xls ...")
    df = pd.read_excel("infoDownload (1).xls")
    print(f"  {len(df)} rows")

    # 2. Load progress
    progress = load_progress()
    tested = progress["tested"]
    start_idx = progress.get("last_index", 0)
    print(f"  Resuming from index {start_idx}, {len(tested)} already tested")

    # 3. Iterate with rate-limit awareness
    total = len(df)
    consecutive_errors = 0

    for idx in range(start_idx, total):
        row = df.iloc[idx]
        isin = str(row.get("ISIN", "")).strip()
        symbol = str(row.get("CLAVE EMISORA", "")).strip()
        name = str(row.get("RAZON SOCIAL", "")).strip()
        country = str(row.get("PAIS DE ORIGEN", "")).strip()
        exchange = str(row.get("BOLSA DONDE COTIZA", "")).strip()

        # Skip if no ISIN or already tested
        if not isin or isin == "nan":
            continue
        if isin in tested:
            continue

        # Test
        ok, err = test_isin(isin, symbol)

        tested[isin] = {
            "symbol": symbol,
            "name": name,
            "country": country,
            "exchange": exchange,
            "ok": ok,
            "error": err if not ok else "",
        }

        # Rate-limit handling
        if not ok and "Rate" in err:
            # Rate limited — backoff
            backoff = min(BACKOFF_BASE * (2 ** consecutive_errors), MAX_BACKOFF)
            consecutive_errors += 1
            print(f"  [{idx+1}/{total}] {symbol} ({isin}): RATE LIMITED, backing off {backoff:.0f}s (consecutive={consecutive_errors})")
            time.sleep(backoff)
            # Retry once after backoff
            ok, err = test_isin(isin, symbol)
            tested[isin]["ok"] = ok
            tested[isin]["error"] = err if not ok else ""
        elif not ok:
            consecutive_errors = 0
        else:
            consecutive_errors = 0

        status = "OK" if ok else f"FAIL({err[:40]})"
        print(f"  [{idx+1}/{total}] {symbol} ({isin}): {status}")

        # Save progress every 50 tickers
        if (idx + 1) % 50 == 0:
            progress["last_index"] = idx + 1
            progress["tested"] = tested
            save_progress(progress)
            ok_count = sum(1 for v in tested.values() if v["ok"])
            print(f"  --- checkpoint: {ok_count}/{len(tested)} OK so far ---")

        # Delay between requests
        time.sleep(DELAY)

    # 4. Final save
    progress["last_index"] = total
    progress["tested"] = tested
    save_progress(progress)

    # 5. Summary
    ok_count = sum(1 for v in tested.values() if v["ok"])
    fail_count = len(tested) - ok_count
    print(f"\n{'='*60}")
    print(f"VALIDATION COMPLETE: {ok_count} OK, {fail_count} FAILED (out of {len(tested)} tested)")
    print(f"{'='*60}")

    # Save final results
    working = {
        isin: {k: v for k, v in meta.items() if k != "ok" and k != "error"}
        for isin, meta in tested.items()
        if meta["ok"]
    }
    failed = {
        isin: {k: v for k, v in meta.items() if k != "ok" and k != "error"}
        for isin, meta in tested.items()
        if not meta["ok"]
    }

    results = {
        "working": working,
        "failed": failed,
        "total_tested": len(tested),
        "working_count": len(working),
        "failed_count": len(failed),
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()