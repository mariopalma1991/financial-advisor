"""News ingestion job: RSS feeds -> raw -> curated (ticker linking + sentiment).

Runs 2-3x daily (plan §11). Idempotent: raw upserts ignore duplicates;
curated upserts insert only on new article_id.

Tiered processing:
- Screening tier: headline + metadata + headline sentiment (FinBERT).
- Analysis tier: full body text + embeddings (Phase 2b, Qdrant integration).
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.rss_news import RssNewsAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw
from src.transforms.news_sentiment import process


def main() -> None:
    with job_run("ingest_news") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        # Fetch articles from the last 2 days (RSS feeds are small windows)
        since = date.today() - timedelta(days=2)
        adapter = RssNewsAdapter()
        articles = adapter.fetch(since=since)
        em.emit(
            "INFO",
            "source_fetched",
            "rss articles",
            payload={"rows": len(articles)},
        )

        if not articles:
            em.emit("INFO", "job_completed", "no new articles")
            return

        # Raw zone: immutable snapshot
        n_raw = raw.insert_news_articles(articles)
        em.emit("INFO", "rows_written", "raw_news_articles", payload={"rows": n_raw})

        # Transform: ticker linking + sentiment scoring + daily aggregation
        curated_articles, daily_sentiment = process(articles, symbols)
        em.emit(
            "INFO",
            "transform",
            "ticker linking + sentiment",
            payload={
                "articles_linked": len(curated_articles),
                "daily_rows": len(daily_sentiment),
            },
        )

        # Curated zone
        n_curated = curated.upsert_news_articles(curated_articles)
        n_sentiment = curated.upsert_news_sentiment(daily_sentiment)
        em.emit(
            "INFO",
            "rows_written",
            "news curated",
            payload={
                "dim_tickers_synced": synced,
                "news_articles_new": n_curated,
                "sentiment_rows_new": n_sentiment,
            },
        )


if __name__ == "__main__":
    main()