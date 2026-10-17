"""News ingestion job: Finnhub -> raw_news_articles.

Runs 3x daily (plan §11). Idempotent: raw upserts ignore duplicates.
New articles are enriched before insert (src/transforms/news_scoring.py): payload
gets `about` (headline or summary names the company) and a FinBERT `sentiment`;
the summary itself goes to the Parquet archive (news_summaries), not Postgres.

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
from src.storage import archive, curated, raw
from src.transforms.news_scoring import article_text, finbert_scores, is_about


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
    patterns = curated.alias_patterns()
    aliases = curated.sync_ticker_aliases(patterns)
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

    known = curated.existing_news_ids([a.article_id for a in all_articles])
    new = [a for a in all_articles if a.article_id not in known]
    if not new:
        em.emit("INFO", "no_new_rows", "raw_news_articles: every article already stored")
        return

    stats = enrich(new, patterns)
    em.emit("INFO", "news_enriched", "relevance + sentiment", payload=stats)
    archived = archive_summaries(new)
    n_raw = raw.insert_news_articles(new)
    em.emit(
        "INFO",
        "rows_written",
        "raw_news_articles",
        payload={"rows": n_raw, "summaries_archived": archived,
                 "dim_tickers_synced": synced, "ticker_aliases": aliases},
    )


def enrich(articles, patterns: dict[str, list[tuple[str, bool]]]) -> dict[str, float]:
    """Add about / sentiment to each article's payload (in place); return coverage."""
    texts = [article_text(a.headline, a.body) for a in articles]
    scores = finbert_scores(texts)
    for a, text, score in zip(articles, texts, scores):
        a.payload["about"] = is_about(text, patterns.get(a.payload.get("related") or "", []))
        a.payload["sentiment"] = score
        a.payload["sentiment_model"] = "finbert"
    n = len(articles) or 1
    return {
        "articles": len(articles),
        "with_summary": round(sum(bool((a.body or "").strip()) for a in articles) / n, 3),
        "about": round(sum(a.payload["about"] for a in articles) / n, 3),
        "avg_sentiment": round(sum(scores) / n, 3),
    }


def archive_summaries(articles) -> int:
    """Summaries to Parquet, one partition per publication day (UTC)."""
    by_day: dict[str, list[dict]] = {}
    for a in articles:
        if (a.body or "").strip():
            by_day.setdefault(a.published_at.date().isoformat(), []).append({
                "article_id": a.article_id, "ticker": a.payload.get("related"),
                "published_at": a.published_at, "headline": a.headline, "summary": a.body,
                "url": a.url,
            })
    s3 = archive.s3_client()
    added, sizes = 0, {}
    for day, rows in sorted(by_day.items()):
        n, size = archive.upsert_partition("news_summaries", day, rows, key=("article_id",), s3=s3)
        added += n
        sizes[day] = size
    if sizes:
        archive.update_manifest("news_summaries", sizes, "published_date", s3=s3)
    return added


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
