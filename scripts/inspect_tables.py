"""Inspect all tables (except pipeline_events) for coverage and freshness."""
from src.storage.supabase_client import pg_connection

TABLES = {
    "raw_price_bars": ("ticker", "bar_date"),
    "raw_corporate_actions": ("ticker", "ex_date"),
    "raw_news_articles": ("source", "published_at"),
    "raw_filings": ("ticker", "filed_at"),
    "raw_financial_reports": ("ticker", "end_date"),
    "raw_earnings_surprises": ("ticker", "period"),
    "raw_earnings_calendar": ("ticker", "event_date"),
    "raw_visa_applications": ("ticker", "received_date"),
    "price_bars_daily": ("ticker", "bar_date"),
    "dim_ticker": ("ticker", None),
}

print(f"{'TABLE':30s} {'ROWS':>10s} {'TICKERS':>8s} {'MIN DATE':>12s} {'MAX DATE':>12s} {'STATUS'}")
print("-" * 90)

with pg_connection() as conn:
    cur = conn.cursor()
    for table, (ticker_col, date_col) in TABLES.items():
        try:
            # Row count
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            row_count = cur.fetchone()[0]

            # Distinct tickers
            cur.execute(f'SELECT COUNT(DISTINCT {ticker_col}) FROM "{table}"')
            ticker_count = cur.fetchone()[0]

            # Date range
            if date_col:
                cur.execute(f'SELECT MIN({date_col}), MAX({date_col}) FROM "{table}"')
                min_d, max_d = cur.fetchone()
                min_str = str(min_d)[:10] if min_d else "N/A"
                max_str = str(max_d)[:10] if max_d else "N/A"
            else:
                min_str = max_str = "N/A"

            # Status
            if row_count == 0:
                status = "!! EMPTY"
            elif ticker_count < 1141 and table.startswith("raw_"):
                status = f"!! {ticker_count}/1141 tickers"
            elif ticker_count >= 1141:
                status = "OK"
            else:
                status = ""

            print(f"  {table:28s} {row_count:>10d} {ticker_count:>8d} {min_str:>12s} {max_str:>12s} {status}")
        except Exception as e:
            print(f"  {table:28s} ERROR: {e}")

print("-" * 90)
print("Target universe: 1,141 tickers")