"""News ingestion job: Finnhub -> raw_news_articles.

Runs 3x daily (plan §11). Idempotent: raw upserts ignore duplicates;
no curated/sentiment stage (those tables were dropped).

Rate limit: 60 Finnhub calls/min, one call per symbol. Each run fetches a
rotating slice of the universe inside a time budget (src/jobs/_symbol_loop.py);
limits live in config/platform.yaml -> ingestion.news.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.adapters.finnhub_news import FinnhubNewsAdapter
from src.config import all_symbols, load_platform
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import EventEmitter, job_run
from src.storage import curated, raw


def run(
    em: EventEmitter,
    _from: str,
    *,
    max_symbols: int | None = None,
    budget_s: float,
) -> None:
    """Fetch news published on/after `_from` and write it to raw_news_articles.

    `max_symbols=None` fetches the whole universe (backfill); the scheduled job
    passes the per-run slice from config.
    """
    cfg = load_platform().ingestion["news"]
    synced = curated.sync_dim_tickers()
    symbols = all_symbols()

    to = date.today().isoformat()
    adapter = FinnhubNewsAdapter()

    res = fetch_symbols(
        symbols,
        lambda s: adapter.fetch(s, _from=_from, to=to),
        em=em,
        label="finnhub news",
        delay_s=float(cfg["call_delay_s"]),
        max_symbols=max_symbols or len(symbols),
        budget_s=budget_s,
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
        em.emit("INFO", "no_new_rows", "raw_news_articles: no new articles")
        return

    # Raw zone only: the curated news tables (news_articles,
    # news_sentiment_daily) were dropped; Finnhub raw tables are the store.
    n_raw = raw.insert_news_articles(all_articles)
    em.emit(
        "INFO",
        "rows_written",
        "raw_news_articles",
        payload={"rows": n_raw, "dim_tickers_synced": synced},
    )


def main() -> None:
    cfg = load_platform().ingestion["news"]
    _from = (date.today() - timedelta(days=int(cfg["lookback_days"]))).isoformat()
    with job_run("ingest_news") as em:
        run(
            em,
            _from,
            max_symbols=int(cfg["max_symbols_per_run"]),
            budget_s=float(cfg["fetch_budget_min"]) * 60,
        )


if __name__ == "__main__":
    main()
