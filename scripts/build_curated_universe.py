
"""Build curated_universe.yaml from screening universe + Finnhub coverage data.

Filters to tickers with ALL THREE: market data + Finnhub news + SEC filings.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_screening_universe

# Load the all_three tickers from cross_coverage
with open("scripts/cross_coverage.json", "r", encoding="utf-8") as f:
    cross = json.load(f)

# Load the news + SEC progress to get the exact ticker sets
with open("scripts/finnhub_news_coverage_progress.json", "r", encoding="utf-8") as f:
    news_data = json.load(f)
with open("scripts/finnhub_sec_coverage_progress.json", "r", encoding="utf-8") as f:
    sec_data = json.load(f)

news_tickers = {k for k, v in news_data.items() if v is True}
sec_tickers = {k for k, v in sec_data.items() if v is True}
all_three = news_tickers & sec_tickers  # all have market data from ISIN validation

print(f"All three (mkt + news + SEC): {len(all_three)}")

# Get full metadata from screening universe
universe = load_screening_universe()
ticker_map = {t.symbol: t for t in universe.tickers}

# Build curated tickers
curated = []
missing = []
for sym in sorted(all_three):
    t = ticker_map.get(sym)
    if t:
        curated.append(t)
    else:
        missing.append(sym)

print(f"With metadata: {len(curated)}")
print(f"Missing from screening universe: {len(missing)}")

# Write YAML
lines = [
    "# CURATED UNIVERSE",
    "# Tickers with market data + Finnhub news + SEC filings.",
    f"# Total: {len(curated)} tickers",
    "",
    "tickers:",
]

# Group by exchange for readability
by_exchange: dict[str, list] = {}
for t in curated:
    by_exchange.setdefault(t.exchange, []).append(t)

exchange_order = ["NYSE", "NASDAQ", "TORONTO STOCK EXCHANGE", "TSE", "OVER THE COUNTER US"]
for ex in exchange_order:
    tickers = by_exchange.pop(ex, [])
    if not tickers:
        continue
    lines.append(f"  # --- {ex} ---")
    for t in tickers:
        name = t.name.replace('"', "'")
        lines.append(
            f'  - {{ symbol: "{t.symbol}", name: "{name}", exchange: {t.exchange}, '
            f'currency: {t.currency}, sector: {t.sector}, benchmark: {t.benchmark}, enabled: true }}'
        )

# Remaining exchanges
for ex, tickers in sorted(by_exchange.items()):
    lines.append(f"  # --- {ex} ---")
    for t in tickers:
        name = t.name.replace('"', "'")
        lines.append(
            f'  - {{ symbol: "{t.symbol}", name: "{name}", exchange: {t.exchange}, '
            f'currency: {t.currency}, sector: {t.sector}, benchmark: {t.benchmark}, enabled: true }}'
        )

with open("config/curated_universe.yaml", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"\nWrote {len(curated)} tickers to config/curated_universe.yaml")