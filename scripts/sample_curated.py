"""Print sample rows from each curated table in Supabase."""
from src.storage.supabase_client import pg_connection


CURATED_TABLES = [
    "corporate_actions",
    "fundamentals_quarterly",
    "indicators_daily",
    "news_articles",
    "news_sentiment_daily",
    "price_bars_daily",
    "research_packages",
    "screener_results",
    "catalyst_events",
    "dim_ticker",
]


def main() -> None:
    for table in CURATED_TABLES:
        print("=" * 100)
        print(f"TABLE: {table}")
        print("=" * 100)
        try:
            with pg_connection() as conn:
                cur = conn.cursor()
                cur.execute(f'SELECT * FROM "{table}" LIMIT 3')
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            if not rows:
                print("  (no rows)")
                continue
            print(f"  COLUMNS: {', '.join(cols)}")
            for i, row in enumerate(rows, 1):
                print(f"  --- row {i} ---")
                for col, val in zip(cols, row):
                    # Truncate long values for readability
                    sval = str(val)
                    if len(sval) > 120:
                        sval = sval[:117] + "..."
                    print(f"    {col}: {sval}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR: {e}")
        print()


if __name__ == "__main__":
    main()
