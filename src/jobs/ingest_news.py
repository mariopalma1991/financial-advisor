"""News ingestion job: Finnhub -> raw -> curated (ticker linking + sentiment).

Runs 2-3x daily (plan §11). Idempotent: raw upserts ignore duplicates;
curated upserts insert only on new article_id.

Rate limit: 60 Finnhub calls/min. Each symbol = 1 call.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from src.adapters.finnhub_news import FinnhubNewsAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw
from src.transforms.news_sentiment import process

# Rate limit: 60 calls/min → 1.1s between calls
_CALL_DELAY = 1.1


def main() -> None:
    with job_run("ingest_news") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        _from = (date.today() - timedelta(days=7)).isoformat()
        to = date.today().isoformat()
        adapter = FinnhubNewsAdapter()

        all_articles = []
        for i, symbol in enumerate(symbols):
            try:
                articles = adapter.fetch(symbol, _from=_from, to=to)
                all_articles.extend(articles)
            except Exception:
                pass
            if i < len(symbols) - 1:
                time.sleep(_CALL_DELAY)

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub news",
            payload={"rows": len(all_articles), "symbols": len(symbols)},
        )

        if not all_articles:
            em.emit("INFO", "job_completed", "no new articles")
            return

        # Raw zone: immutable snapshot
        n_raw = raw.insert_news_articles(all_articles)
        em.emit("INFO", "rows_written", "raw_news_articles", payload={"rows": n_raw})

        # Transform: ticker linking + sentiment scoring + daily aggregation
        curated_articles, daily_sentiment = process(all_articles, symbols)
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