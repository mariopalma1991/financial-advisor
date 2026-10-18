"""Raw zone writers — insert-only, bitemporal, idempotent (plan §6).

Idempotency via unique natural keys + upsert-ignore: re-running a job never
duplicates rows. Genuine source corrections surface in the curated zone through
change detection (src/storage/curated.py), preserving the audit trail there.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from src.adapters.base import (
    RawCorporateAction,
    RawEarningsCalendar,
    RawEarningsSurprise,
    RawFiling,
    RawInsiderSentiment,
    RawNewsArticle,
    RawPriceBar,
)
from src.quality.run_metadata import current_run_id
from src.storage.supabase_client import get_client, pg_connection


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
            "run_id": current_run_id(),
        }
        for b in bars
    ]
    for i in range(0, len(rows), 1000):
        get_client().table("raw_price_bars").upsert(
            rows[i : i + 1000], on_conflict="source,ticker,bar_date", ignore_duplicates=True
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
            "run_id": current_run_id(),
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
            "source": f.source,
            "accession_no": f.accession_no,
            "ticker": f.ticker,
            "form_type": f.form_type,
            "filed_at": f.filed_at.isoformat(),
            "url": f.url,
            "payload_json": f.payload,
            "valid_from": f.filed_at.isoformat(),
            "ingested_at": _now(),
            "run_id": current_run_id(),
        }
        for f in filings
    ]
    for i in range(0, len(rows), 1000):
        get_client().table("raw_filings").upsert(
            rows[i : i + 1000], on_conflict="accession_no", ignore_duplicates=True
        ).execute()
    return len(rows)


def delete_filings_except(keep_forms: list[str]) -> int:
    """Delete filings whose form is not in keep_forms; returns rows deleted."""
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("delete from raw_filings where form_type <> all(%s)", (keep_forms,))
        return cur.rowcount


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
            "run_id": current_run_id(),
        }
        for a in articles
    ]
    for i in range(0, len(rows), 1000):
        get_client().table("raw_news_articles").upsert(
            rows[i : i + 1000], on_conflict="source,article_id", ignore_duplicates=True
        ).execute()
    return len(rows)


def insert_earnings_surprises(surprises: list[RawEarningsSurprise]) -> int:
    """Insert raw earnings surprises; idempotent on (source, ticker, period)."""
    if not surprises:
        return 0
    rows = [
        {
            "source": s.source,
            "ticker": s.ticker,
            "period": s.period.isoformat(),
            "quarter": s.quarter,
            "year": s.year,
            "actual": s.actual,
            "estimate": s.estimate,
            "surprise": s.surprise,
            "surprise_pct": s.surprise_pct,
            "payload_json": s.payload,
            "valid_from": s.period.isoformat(),
            "ingested_at": _now(),
            "run_id": current_run_id(),
        }
        for s in surprises
    ]
    get_client().table("raw_earnings_surprises").upsert(
        rows, on_conflict="source,ticker,period", ignore_duplicates=True
    ).execute()
    return len(rows)


def insert_earnings_calendar(events: list[RawEarningsCalendar]) -> int:
    """Upsert raw earnings calendar events on (source, ticker, event_date, quarter)."""
    if not events:
        return 0
    rows = [
        {
            "source": e.source,
            "ticker": e.ticker,
            "event_date": e.date.isoformat(),
            "quarter": e.quarter,
            "year": e.year,
            "hour": e.hour,
            "eps_actual": e.eps_actual,
            "eps_estimate": e.eps_estimate,
            "revenue_actual": e.revenue_actual,
            "revenue_estimate": e.revenue_estimate,
            "payload_json": e.payload,
            "valid_from": e.date.isoformat(),
            "ingested_at": _now(),
            "run_id": current_run_id(),
        }
        for e in events
    ]
    # Overwrite: a report seen as upcoming gets its actuals (and revised estimates)
    # on a later run; rescheduled dates land as new rows (current_earnings_calendar).
    get_client().table("raw_earnings_calendar").upsert(
        rows, on_conflict="source,ticker,event_date,quarter"
    ).execute()
    return len(rows)


def insert_insider_sentiment(rows_in: list[RawInsiderSentiment]) -> int:
    """Insert monthly insider sentiment; idempotent on the month *and* its values.

    The current month keeps changing as new Form 4s are filed, so the natural
    key includes change and mspr: an identical re-fetch is a no-op, while a
    revised month lands as a new row with a later valid_from (point-in-time
    readers keep the latest version known as of a date).
    """
    if not rows_in:
        return 0
    now = _now()
    rows = [
        {
            "source": r.source,
            "ticker": r.ticker,
            "period": date(r.year, r.month, 1).isoformat(),
            "year": r.year,
            "month": r.month,
            "change": r.change,
            "mspr": r.mspr,
            "payload_json": r.payload,
            "valid_from": now,
            "ingested_at": now,
            "run_id": current_run_id(),
        }
        for r in rows_in
    ]
    get_client().table("raw_insider_sentiment").upsert(
        rows, on_conflict="source,ticker,period,change,mspr", ignore_duplicates=True
    ).execute()
    return len(rows)
