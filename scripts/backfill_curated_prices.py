"""Clean and backfill price_bars_daily from raw_price_bars.

Deduplicates raw_price_bars to one row per (ticker, bar_date):
- Prefers 'yfinance' source over others
- Within same source, picks latest ingested_at
- Only includes rows with non-null close
"""
from src.storage.supabase_client import pg_connection


SQL_TRUNCATE = 'TRUNCATE TABLE price_bars_daily RESTART IDENTITY CASCADE'

SQL_BACKFILL = """
INSERT INTO price_bars_daily (ticker, bar_date, open, high, low, close, volume, source_used, valid_from, ingested_at)
SELECT
    ticker,
    bar_date,
    open,
    high,
    low,
    close,
    volume,
    source AS source_used,
    valid_from,
    NOW() AS ingested_at
FROM (
    SELECT DISTINCT ON (ticker, bar_date)
        ticker,
        bar_date,
        open,
        high,
        low,
        close,
        volume,
        source,
        valid_from,
        ingested_at
    FROM raw_price_bars
    WHERE close IS NOT NULL
    ORDER BY
        ticker,
        bar_date,
        CASE source WHEN 'yfinance' THEN 0 ELSE 1 END,  -- prefer yfinance
        ingested_at DESC
) AS deduped
ORDER BY ticker, bar_date
"""


def main() -> None:
    print("Truncating price_bars_daily...")
    with pg_connection() as conn:
        cur = conn.cursor()
        cur.execute(SQL_TRUNCATE)
        print("  Truncated.")

        print("Backfilling from raw_price_bars (dedup: 1 row per ticker/date, prefer yfinance)...")
        cur.execute(SQL_BACKFILL)
        inserted = cur.rowcount
        print(f"  Inserted {inserted} rows into price_bars_daily.")

    # Verify
    with pg_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM price_bars_daily")
        curated_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT (ticker, bar_date)) FROM raw_price_bars WHERE close IS NOT NULL")
        distinct_raw = cur.fetchone()[0]
        print(f"\nVerification:")
        print(f"  price_bars_daily: {curated_count} rows")
        print(f"  raw distinct (ticker, bar_date): {distinct_raw}")
        print(f"  Match: {'OK' if curated_count == distinct_raw else 'MISMATCH'}")


if __name__ == "__main__":
    main()