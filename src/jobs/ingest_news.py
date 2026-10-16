"""News ingestion job: Finnhub -> raw -> curated (ticker linking + sentiment).

Runs 3x daily (plan §11). Idempotent: raw upserts ignore duplicates;
curated upserts insert only on new article_id.

Rate limit: 60 Finnhub calls/min, one call per symbol. Each run fetches a
rotating slice of the universe inside a time budget (src/jobs/_symbol_loop.py);
limits live in config/platform.yaml -> ingestion.news. Articles already stored
are not re-scored by FinBERT.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.finnhub_news import FinnhubNewsAdapter
from src.config import all_symbols, load_platform
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw
from src.transforms.news_sentiment import process


def main() -> None:
    cfg = load_platform().ingestion["news"]
    with job_run("ingest_news") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        _from = (date.today() - timedelta(days=int(cfg["lookback_days"]))).isoformat()
        to = date.today().isoformat()
        adapter = FinnhubNewsAdapter()

        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch(s, _from=_from, to=to),
            em=em,
            label="finnhub news",
            delay_s=float(cfg["call_delay_s"]),
            max_symbols=int(cfg["max_symbols_per_run"]),
            budget_s=float(cfg["fetch_budget_min"]) * 60,
        )
        # The same article comes back once per symbol it mentions.
        all_articles = list({a.article_id: a for a in res.items}.values())

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub news",
            payload={"rows": len(all_articles), "symbols": len(res.processed)},
        )

        if not all_articles:
            em.emit("INFO", "job_completed", "no new articles")
            return

        # Raw zone: immutable snapshot
        n_raw = raw.insert_news_articles(all_articles)
        em.emit("INFO", "rows_written", "raw_news_articles", payload={"rows": n_raw})

        # Transform: ticker linking + sentiment (new articles only) + daily aggregation
        known = curated.get_news_scores([a.article_id for a in all_articles])
        curated_articles, daily_sentiment = process(
            all_articles,
            symbols,
            known_scores=known,
            aggregate_tickers=set(res.processed),
        )
        new_articles = [a for a in curated_articles if a["article_id"] not in known]
        em.emit(
            "INFO",
            "transform",
            "ticker linking + sentiment",
            payload={
                "articles_linked": len(curated_articles),
                "articles_scored": len(new_articles),
                "daily_rows": len(daily_sentiment),
            },
        )

        # Curated zone
        n_curated = curated.upsert_news_articles(new_articles)
        n_sentiment = curated.upsert_news_sentiment(daily_sentiment)
        em.emit(
            "INFO",
            "rows_written",
            "news curated",
            payload={
                "dim_tickers_synced": synced,
                "news_articles_new": n_curated,
                "sentiment_rows_written": n_sentiment,
            },
        )


if __name__ == "__main__":
    main()
