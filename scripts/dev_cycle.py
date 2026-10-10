"""Run one full local pipeline cycle for a few tickers (dev / smoke test).

Usage: python scripts/dev_cycle.py AAPL MSFT
Requires .env with Supabase credentials.
"""
from __future__ import annotations

import subprocess
import sys


def _run(module: str, *args: str) -> None:
    print(f"\n=== {module} {' '.join(args)} ===", flush=True)
    subprocess.run([sys.executable, "-m", module, *args], check=True)


def main() -> None:
    tickers = sys.argv[1:] or ["AAPL", "MSFT"]
    _run("src.jobs.backfill", *tickers)       # prices + corporate actions + indicators
    _run("src.jobs.ingest_filings")           # EDGAR fundamentals + filing metadata
    _run("src.jobs.run_screener")             # screens over both tiers
    _run("src.jobs.build_feeds")              # versioned feeds for the analysis tier
    print("\nCycle complete. Query via MCP: python -m src.mcp_server.server")


if __name__ == "__main__":
    main()
