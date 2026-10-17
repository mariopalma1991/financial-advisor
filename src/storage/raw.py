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
    RawFinancialReport,
    RawInsiderSentiment,
    RawNewsArticle,
    RawPriceBar,
    RawVisaApplication,
)
from src.quality.run_metadata import current_run_id
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
            "run_id": current_run_id(),
        }
        for a in articles
    ]
    for i in range(0, len(rows), 1000):
        get_client().table("raw_news_articles").upsert(
            rows[i : i + 1000], on_conflict="source,article_id", ignore_duplicates=True
        ).execute()
    return len(rows)


def insert_financial_reports(reports: list[RawFinancialReport]) -> int:
    """Insert raw financial reports; idempotent on (source, ticker, access_number)."""
    if not reports:
        return 0
    rows = [
        {
            "source": r.source,
            "ticker": r.ticker,
            "cik": r.cik,
            "access_number": r.access_number,
            "form": r.form,
            "year": r.year,
            "quarter": r.quarter,
            "start_date": r.start_date.isoformat() if r.start_date else None,
            "end_date": r.end_date.isoformat() if r.end_date else None,
            "filed_date": r.filed_date.isoformat() if r.filed_date else None,
            "report_json": r.report,
            "payload_json": r.payload,
            "valid_from": (r.filed_date or datetime.now(timezone.utc)).isoformat(),
            "ingested_at": _now(),
            "run_id": current_run_id(),
        }
        for r in reports
    ]
    # Each report carries its full statements as JSON: one request with thousands of
    # them hit Postgres' statement timeout, so write in small chunks.
    for i in range(0, len(rows), 100):
        get_client().table("raw_financial_reports").upsert(
            rows[i : i + 100], on_conflict="source,ticker,access_number", ignore_duplicates=True
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
    """Insert raw earnings calendar events; idempotent on (source, ticker, event_date, quarter)."""
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
    get_client().table("raw_earnings_calendar").upsert(
        rows, on_conflict="source,ticker,event_date,quarter", ignore_duplicates=True
    ).execute()
    return len(rows)


def insert_visa_applications(apps: list[RawVisaApplication]) -> int:
    """Insert raw visa applications; idempotent on (source, ticker, case_number)."""
    if not apps:
        return 0
    rows = [
        {
            "source": a.source,
            "ticker": a.ticker,
            "case_number": a.case_number,
            "job_title": a.job_title,
            "visa_class": a.visa_class,
            "case_status": a.case_status,
            "employer_name": a.employer_name,
            "begin_date": a.begin_date.isoformat() if a.begin_date else None,
            "end_date": a.end_date.isoformat() if a.end_date else None,
            "received_date": a.received_date.isoformat() if a.received_date else None,
            "quarter": a.quarter,
            "year": a.year,
            "wage_range_from": a.wage_range_from,
            "wage_range_to": a.wage_range_to,
            "wage_unit": a.wage_unit,
            "worksite_city": a.worksite_city,
            "worksite_state": a.worksite_state,
            "full_time": a.full_time,
            "h1b_dependent": a.h1b_dependent,
            "payload_json": a.payload,
            "valid_from": (a.received_date or datetime.now(timezone.utc)).isoformat(),
            "ingested_at": _now(),
            "run_id": current_run_id(),
        }
        for a in apps
    ]
    get_client().table("raw_visa_applications").upsert(
        rows, on_conflict="source,ticker,case_number", ignore_duplicates=True
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
