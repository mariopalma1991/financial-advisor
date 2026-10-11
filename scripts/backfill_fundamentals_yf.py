"""Backfill 3 years of quarterly fundamentals from yfinance for curated tickers."""
import sys
sys.path.insert(0, ".")

from datetime import date

import pandas as pd

from src.config import all_symbols
from src.storage import curated
from src.storage.supabase_client import get_client

YEARS = 3
TICKERS = all_symbols()


def fetch_fundamentals(ticker: str) -> list[dict]:
    """Fetch quarterly fundamentals from yfinance and return rows for DB."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    qf = t.quarterly_financials
    bs = t.quarterly_balance_sheet

    if qf.empty:
        print(f"    [{ticker}] no quarterly financials")
        return []

    rows = []
    for col in qf.columns:
        period_end = col.date()
        # Only include last YEARS years
        if period_end < date.today().replace(year=date.today().year - YEARS):
            continue

        def _get(df, key, default=None):
            if key in df.index:
                val = df.loc[key, col]
                return float(val) if pd.notna(val) else None
            return default

        revenue = _get(qf, "Total Revenue")
        net_income = _get(qf, "Net Income From Continuing Operation Net Minority Interest") or _get(qf, "Net Income")
        eps = _get(qf, "Diluted EPS") or _get(qf, "Basic EPS")
        gross_profit = _get(qf, "Gross Profit")
        cost_of_revenue = _get(qf, "Reconciled Cost Of Revenue") or _get(qf, "Cost Of Revenue")

        # Gross margin
        gross_margin = None
        if revenue and gross_profit:
            gross_margin = round(gross_profit / revenue, 6)
        elif revenue and cost_of_revenue:
            gross_margin = round((revenue - cost_of_revenue) / revenue, 6)

        # Net margin
        net_margin = None
        if revenue and net_income:
            net_margin = round(net_income / revenue, 6)

        # Balance sheet
        total_debt = None
        equity = None
        if not bs.empty and col in bs.columns:
            total_debt = _get(bs, "Total Debt")
            equity = _get(bs, "Total Equity Gross Minority Interest") or _get(bs, "Stockholders Equity")

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        rows.append({
            "ticker": ticker,
            "period_end": str(period_end),
            "revenue": revenue,
            "net_income": net_income,
            "eps": eps,
            "total_debt": total_debt,
            "equity": equity,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "growth_yoy": None,
            "valid_from": now,
            "ingested_at": now,
        })

    return rows


def main() -> None:
    print(f"Fetching {YEARS}y fundamentals for {len(TICKERS)} tickers via yfinance...")

    all_rows = []
    for ticker in TICKERS:
        print(f"  [{ticker}] ...", end=" ", flush=True)
        rows = fetch_fundamentals(ticker)
        print(f"{len(rows)} quarters")
        all_rows.extend(rows)

    if all_rows:
        n = curated.upsert_fundamentals(all_rows)
        print(f"\nUpserted {n} fundamental rows for {len(TICKERS)} tickers")
    else:
        print("\nNo fundamentals fetched")


if __name__ == "__main__":
    main()