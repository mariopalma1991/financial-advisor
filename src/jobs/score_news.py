"""One-off / catch-up FinBERT scoring of stored articles that have no sentiment yet.

Articles stored before ingest-time scoring have no summary (it was discarded), so
these are scored on the headline alone and tagged sentiment_model=finbert-headline.
Usage: python -m src.jobs.score_news --days 30
"""
from __future__ import annotations

import argparse
import json

from src.quality.run_metadata import job_run
from src.storage.supabase_client import pg_connection
from src.transforms.news_scoring import finbert_scores

_CHUNK = 1000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    with job_run("score_news") as em, pg_connection() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "select id, headline from raw_news_articles "
                "where published_at >= now() - make_interval(days => %s) "
                "and not payload_json ? 'sentiment' and coalesce(headline, '') <> '' "
                "order by published_at desc",
                (args.days,),
            )
            todo = cur.fetchall()
            scored = 0
            for i in range(0, len(todo), _CHUNK):
                chunk = todo[i : i + _CHUNK]
                scores = finbert_scores([h for _, h in chunk])
                cur.executemany(
                    "update raw_news_articles set payload_json = payload_json || %s::jsonb where id = %s",
                    [(json.dumps({"sentiment": s, "sentiment_model": "finbert-headline"}), id_)
                     for (id_, _), s in zip(chunk, scores)],
                )
                scored += len(chunk)
        em.emit("INFO", "rows_written", "raw_news_articles sentiment",
                payload={"rows": scored, "days": args.days})


if __name__ == "__main__":
    main()
