"""Check raw_corporate_actions coverage."""
from src.storage.supabase_client import pg_connection

with pg_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT ticker) FROM raw_corporate_actions")
    print(f"Distinct tickers: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM raw_corporate_actions")
    print(f"Total rows: {cur.fetchone()[0]}")