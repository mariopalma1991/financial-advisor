"""Test all BMV-listed tickers (from Excel files) with yfinance.
Fetches 2 days of data per ticker; keeps only those that return data.
Outputs a JSON file with working tickers and their metadata.
"""
import json
import sys
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

BATCH_SIZE = 50
DAYS = 5  # fetch a few days to be safe
SLEEP_BETWEEN_BATCHES = 0.5  # seconds

def main() -> None:
    # 1. Load both Excel files
    df1 = pd.read_excel("infoDownload.xls")
    df2 = pd.read_excel("infoDownload (1).xls")

    # Build lookup: symbol -> {name, country, exchange}
    meta: dict[str, dict] = {}

    for _, row in df1.iterrows():
        sym = str(row["CLAVE EMISORA"]).strip()
        name = str(row.get("RAZON SOCIAL", "")).strip()
        if sym and sym not in meta:
            meta[sym] = {"name": name, "country": "MEXICO", "exchange": "BMV"}

    for _, row in df2.iterrows():
        sym = str(row["CLAVE EMISORA"]).strip()
        name = str(row.get("RAZON SOCIAL", "")).strip()
        country = str(row.get("PAIS DE ORIGEN", "")).strip()
        exchange = str(row.get("BOLSA DONDE COTIZA", "")).strip()
        if sym and sym not in meta:
            meta[sym] = {"name": name, "country": country, "exchange": exchange}

    all_symbols = sorted(meta.keys())
    print(f"Total unique symbols to test: {len(all_symbols)}")

    # 2. Test in batches
    end_date = date.today()
    start_date = end_date - timedelta(days=DAYS)

    working: dict[str, dict] = {}
    failed: list[str] = []
    total_batches = (len(all_symbols) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(all_symbols), BATCH_SIZE):
        batch = all_symbols[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\nBatch {batch_num}/{total_batches}: testing {len(batch)} symbols...", end=" ", flush=True)

        try:
            df = yf.download(
                tickers=batch,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                progress=False,
                auto_adjust=True,
                threads=True,
            )
        except Exception as e:
            print(f"batch error: {e}")
            failed.extend(batch)
            continue

        if df.empty:
            print("no data for any ticker in batch")
            failed.extend(batch)
            continue

        # df has MultiIndex columns: (Price, Ticker)
        # Check which tickers have at least one non-NaN row
        if isinstance(df.columns, pd.MultiIndex):
            tickers_in_result = set(df.columns.get_level_values(1))
        else:
            # Single ticker case
            tickers_in_result = {batch[0]} if not df.empty else set()

        for sym in batch:
            if sym in tickers_in_result:
                # Check if there's actual data (not all NaN)
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        close_col = ("Close", sym)
                    else:
                        close_col = "Close"
                    if close_col in df.columns and df[close_col].notna().any():
                        working[sym] = meta.get(sym, {})
                        continue
                except Exception:
                    pass
            failed.append(sym)

        batch_ok = len([s for s in batch if s in working])
        print(f"{batch_ok}/{len(batch)} OK")

        if batch_num < total_batches:
            time.sleep(SLEEP_BETWEEN_BATCHES)

    # 3. Report
    print(f"\n{'='*60}")
    print(f"RESULTS: {len(working)} working, {len(failed)} failed")
    print(f"{'='*60}")

    # Show breakdown by country
    countries: dict[str, int] = {}
    for sym, m in working.items():
        c = m.get("country", "UNKNOWN")
        countries[c] = countries.get(c, 0) + 1
    print("\nWorking by country:")
    for c, n in sorted(countries.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")

    # Save results
    output = {
        "working": working,
        "failed": failed,
        "total_tested": len(all_symbols),
        "working_count": len(working),
        "failed_count": len(failed),
    }
    with open("scripts/bmv_test_results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to scripts/bmv_test_results.json")

    # Also save just the working symbols list
    with open("scripts/bmv_working_symbols.txt", "w", encoding="utf-8") as f:
        for sym in sorted(working.keys()):
            f.write(f"{sym}\n")
    print(f"Working symbols saved to scripts/bmv_working_symbols.txt")


if __name__ == "__main__":
    main()