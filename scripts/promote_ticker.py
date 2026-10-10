"""Promote a ticker from the screening tier to the analysis tier.

Usage: python scripts/promote_ticker.py NVDA

Moves the entry from config/screening_universe.yaml to config/tickers.yaml,
then runs the backfill job (which pulls full history + indicators).
Demotion is the reverse: remove from tickers.yaml (history is retained).
"""
from __future__ import annotations

import subprocess
import sys

import yaml

from src.config import CONFIG_DIR

_HEADER = (
    "# ANALYSIS tier — full pipeline (news, filings, feeds, agents).\n"
    "# Criteria and rationale: plans/ticker-universe.md\n"
    "# Note: BMV symbols use yfinance naming (.MX suffix).\n"
)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python scripts/promote_ticker.py SYMBOL [SYMBOL...]")

    screening_path = CONFIG_DIR / "screening_universe.yaml"
    analysis_path = CONFIG_DIR / "tickers.yaml"
    screening = yaml.safe_load(screening_path.read_text(encoding="utf-8"))
    analysis = yaml.safe_load(analysis_path.read_text(encoding="utf-8"))

    by_symbol = {t["symbol"]: t for t in screening["tickers"]}
    existing = {t["symbol"] for t in analysis["tickers"]}

    for symbol in sys.argv[1:]:
        if symbol in existing:
            print(f"{symbol}: already in analysis tier, skipping move")
            continue
        entry = by_symbol.get(symbol)
        if entry is None:
            print(f"{symbol}: not found in screening universe, skipping")
            continue
        analysis["tickers"].append(entry)
        print(f"{symbol}: moved to analysis tier")

    analysis_path.write_text(
        _HEADER + "\n" + yaml.safe_dump(analysis, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    print("\nRunning backfill...")
    subprocess.run([sys.executable, "-m", "src.jobs.backfill", *sys.argv[1:]], check=True)


if __name__ == "__main__":
    main()
