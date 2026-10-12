"""Delete RSS records from raw_news_articles."""
from src.storage.supabase_client import pg_connection

with pg_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM raw_news_articles WHERE source = 'rss'")
    rss_count = cur.fetchone()[0]
    print(f"RSS articles to delete: {rss_count}")

    cur.execute("DELETE FROM raw_news_articles WHERE source = 'rss'")
    print(f"Deleted {cur.rowcount} RSS articles")

    cur.execute("SELECT COUNT(*) FROM raw_news_articles")
    remaining = cur.fetchone()[0]
    print(f"Remaining: {remaining}")

    cur.execute("SELECT DISTINCT source FROM raw_news_articles")
    sources = [r[0] for r in cur.fetchall()]
    print(f"Sources now: {sources}")