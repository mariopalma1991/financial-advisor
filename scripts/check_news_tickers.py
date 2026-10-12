"""Check raw_news_articles ticker coverage via payload_json."""
from src.storage.supabase_client import pg_connection

with pg_connection() as conn:
    cur = conn.cursor()

    # Check payload structure
    cur.execute("SELECT payload_json FROM raw_news_articles LIMIT 1")
    row = cur.fetchone()
    if row:
        print(f"Sample payload keys: {list(row[0].keys()) if row[0] else 'None'}")

    # Try common ticker field names
    for field in ("ticker", "symbol", "tickers", "related"):
        try:
            cur.execute(f"SELECT COUNT(DISTINCT payload_json->>'{field}') FROM raw_news_articles")
            cnt = cur.fetchone()[0]
            print(f"  payload_json->>'{field}': {cnt} distinct values")
        except Exception as e:
            print(f"  payload_json->>'{field}': ERROR {e}")

    # Check source column
    cur.execute("SELECT DISTINCT source FROM raw_news_articles")
    sources = [r[0] for r in cur.fetchall()]
    print(f"Sources: {sources}")

    cur.execute("SELECT COUNT(*) FROM raw_news_articles")
    print(f"Total articles: {cur.fetchone()[0]}")

    # Check if ticker is in a nested field
    cur.execute("SELECT payload_json FROM raw_news_articles LIMIT 3")
    for i, row in enumerate(cur.fetchall()):
        p = row[0]
        # Print first 200 chars
        s = str(p)
        print(f"Row {i}: {s[:200]}")