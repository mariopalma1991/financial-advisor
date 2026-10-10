"""Raw zone writers — insert-only, bitemporal, idempotent (plan §6).

Idempotency via unique natural keys + upsert-ignore: re-running a job never
duplicates rows. Genuine source corrections surface in the curated zone through
change detection (src/storage/curated.py), preserving the audit trail there.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.adapters.base import RawCorporateAction, RawNewsArticle, RawPriceBar
from src.adapters.edgar_filings import RawFiling
from src.storage.supabase_client import get_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_price_bars(bars: list[RawPriceBar]) -> int:
    if not bars:
        return 0
    rows = [
        {
            "source": b.source,
            "ticker": b.ticker,
            "bar_date": b.bar_date.isoformat(),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "payload_json": b.payload,
            "valid_from": (b.valid_from or datetime.now(timezone.utc)).isoformat(),
            "ingested_at": _now(),
        }
        for b in bars
    ]
    get_client().table("raw_price_bars").upsert(
        rows, on_conflict="source,ticker,bar_date", ignore_duplicates=True
    ).execute()
    return len(rows)


def insert_corporate_actions(actions: list[RawCorporateAction]) -> int:
    if not actions:
        return 0
    rows = [
        {
            "source": a.source,
            "ticker": a.ticker,
            "action_type": a.action_type,
            "ex_date": a.ex_date.isoformat(),
            "amount": a.amount,
            "ratio": a.ratio,
            "payload_json": a.payload,
            "valid_from": f"{a.ex_date.isoformat()}T00:00:00+00:00",
            "ingested_at": _now(),
        }
        for a in actions
    ]
    get_client().table("raw_corporate_actions").upsert(
        rows, on_conflict="source,ticker,ex_date,action_type", ignore_duplicates=True
    ).execute()
    return len(rows)


def insert_filings(filings: list[RawFiling]) -> int:
    if not filings:
        return 0
    rows = [
        {
            "source": "sec_edgar",
            "accession_no": f.accession_no,
            "ticker": f.ticker,
            "form_type": f.form_type,
            "filed_at": f.filed_at.isoformat(),
            "url": f.url,
            "payload_json": f.payload,
            "valid_from": f.filed_at.isoformat(),
            "ingested_at": _now(),
        }
        for f in filings
    ]
    get_client().table("raw_filings").upsert(
        rows, on_conflict="accession_no", ignore_duplicates=True
    ).execute()
    return len(rows)


def insert_news_articles(articles: list[RawNewsArticle]) -> int:
    """Insert raw news articles; idempotent on (source, article_id)."""
    if not articles:
        return 0
    rows = [
        {
            "source": a.source,
            "article_id": a.article_id,
            "published_at": a.published_at.isoformat(),
            "url": a.url,
            "headline": a.headline,
            "payload_json": a.payload,
            "valid_from": a.published_at.isoformat(),
            "ingested_at": _now(),
        }
        for a in articles
    ]
    get_client().table("raw_news_articles").upsert(
        rows, on_conflict="source,article_id", ignore_duplicates=True
    ).execute()
    return len(rows)
