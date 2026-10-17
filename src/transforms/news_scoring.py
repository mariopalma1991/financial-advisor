"""Per-article enrichment at ingest time: relevance and FinBERT sentiment.

Finnhub's summary (2-3 sentences) is the only article text we get. It is too big to
keep in Postgres for every article (~28 MB at 90 days on a 500 MB plan), so
ingest_news uses it here, stores the results in the article's payload and archives
the summary itself as Parquet (news_summaries, one file per publication day):

- about: the headline or summary matches one of the tagged ticker's aliases
  (ticker_aliases patterns, translated from Postgres to Python regex);
- sentiment: FinBERT positive minus negative probability on headline + summary, in
  [-1, 1]; sentiment_model says which model produced it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache

from src.transforms.news_sentiment import _load_finbert

MODEL = "ProsusAI/finbert"
_BATCH = 32


def pg_to_py(pattern: str) -> str:
    """Postgres ARE word boundaries (\\m, \\M) -> Python \\b; the rest is shared syntax."""
    return pattern.replace(r"\m", r"\b").replace(r"\M", r"\b")


@cache
def _compiled(pattern: str, case_sensitive: bool) -> re.Pattern[str]:
    return re.compile(pg_to_py(pattern), 0 if case_sensitive else re.IGNORECASE)


def is_about(text: str, patterns: Iterable[tuple[str, bool]]) -> bool:
    return any(_compiled(p, cs).search(text) for p, cs in patterns)


def article_text(headline: str | None, summary: str | None) -> str:
    h, s = (headline or "").strip(), (summary or "").strip()
    return f"{h}. {s}" if h and s else h or s


def finbert_scores(texts: list[str]) -> list[float]:
    """positive - negative per text. Raises if FinBERT can't load: a keyword fallback
    would store a different kind of number under the same column."""
    pipe = _load_finbert()
    if pipe is None:
        raise RuntimeError(f"{MODEL} could not be loaded")
    out: list[float] = []
    for result in pipe(texts, truncation=True, max_length=512, batch_size=_BATCH):
        scores = {r["label"].lower(): r["score"] for r in result}
        out.append(round(scores.get("positive", 0.0) - scores.get("negative", 0.0), 3))
    return out
