"""Backfill all raw tables with 5 years of data for the curated universe.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
Estimated runtime: ~2.5 hours for 1,141 tickers × 7 adapters.

Usage:
    python scripts/backfill_all.py [--adapter price|news|filings|financials|earnings|earncal|visa|all] [--clean]

    --clean   Truncate target tables and reset progress before backfilling.
              Without it, backfill resumes from where it left off (idempotent).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.finnhub_earnings import FinnhubEarningsAdapter
from src.adapters.finnhub_earnings_calendar import FinnhubEarningsCalendarAdapter
from src.adapters.finnhub_filings import FinnhubFilingsAdapter
from src.adapters.finnhub_financials import FinnhubFinancialsAdapter
from src.adapters.finnhub_news import FinnhubNewsAdapter
from src.adapters.finnhub_visa import FinnhubVisaAdapter
from src.adapters.yfinance_prices import YFinanceAdapter
from src.config import load_curated_universe
from src.storage import raw
from src.storage.supabase_client import get_client, pg_connection

CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
PROGRESS_FILE = Path(__file__).resolve().parent / "backfill_progress.json"

SYMBOLS = load_curated_universe().symbols
TODAY = date.today()
FIVE_YEARS_AGO = TODAY - timedelta(days=5 * 365)
ONE_YEAR_AGO = TODAY - timedelta(days=365)
ONE_MONTH_AGO = TODAY - timedelta(days=30)

CLEAN = False  # set by --clean flag


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(p: dict) -> None:
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2)


def truncate_table(table: str) -> None:
    """Truncate a table and reset its identity sequence."""
    with pg_connection() as conn:
        conn.cursor().execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    print(f"  TRUNCATED {table}")


def rebuild_curated_prices() -> int:
    """Rebuild price_bars_daily from raw_price_bars (dedup: 1 row per ticker/date)."""
    sql = """
    INSERT INTO price_bars_daily (ticker, bar_date, open, high, low, close, volume, source_used, valid_from, ingested_at)
    SELECT ticker, bar_date, open, high, low, close, volume, source AS source_used, valid_from, NOW()
    FROM (
        SELECT DISTINCT ON (ticker, bar_date)
            ticker, bar_date, open, high, low, close, volume, source, valid_from, ingested_at
        FROM raw_price_bars
        WHERE close IS NOT NULL
        ORDER BY ticker, bar_date,
            CASE source WHEN 'yfinance' THEN 0 ELSE 1 END,
            ingested_at DESC
    ) AS deduped
    ORDER BY ticker, bar_date
    """
    with pg_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return cur.rowcount


def backfill_prices() -> None:
    """YFinance daily bars + corporate actions — 5 years."""
    print("\n=== PRICES (yfinance) ===")
    if CLEAN:
        truncate_table("raw_price_bars")
        truncate_table("raw_corporate_actions")
        truncate_table("price_bars_daily")
        progress = load_progress()
        progress.pop("prices", None)
        save_progress(progress)

    adapter = YFinanceAdapter()
    progress = load_progress()
    done = set(progress.get("prices", []))
    pending = [s for s in SYMBOLS if s not in done]

    # YFinance can fetch many tickers at once — smaller batches to avoid timeouts
    batch_size = 20
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        try:
            bars = adapter.fetch_bars(batch, FIVE_YEARS_AGO)
            actions = adapter.fetch_corporate_actions(batch, FIVE_YEARS_AGO)
            raw.insert_price_bars(bars)
            raw.insert_corporate_actions(actions)
            done.update(batch)
            progress["prices"] = list(done)
            if (i // batch_size) % 10 == 0:
                save_progress(progress)
            print(f"  prices: {len(done)}/{len(SYMBOLS)} tickers, {len(bars)} bars, {len(actions)} actions")
        except Exception as e:
            print(f"  prices batch error: {e}")
        time.sleep(1.0)

    save_progress(progress)
    print(f"  prices done: {len(done)} tickers")

    # Rebuild curated price_bars_daily from raw
    print("  rebuilding curated price_bars_daily...")
    n = rebuild_curated_prices()
    print(f"  curated price_bars_daily: {n} rows")


def backfill_news() -> None:
    """Finnhub news — free tier: 1 year only."""
    print("\n=== NEWS (finnhub) ===")
    if CLEAN:
        truncate_table("raw_news_articles")
        progress = load_progress()
        progress.pop("news", None)
        save_progress(progress)

    adapter = FinnhubNewsAdapter()
    progress = load_progress()
    done = set(progress.get("news", []))
    pending = [s for s in SYMBOLS if s not in done]

    _from = ONE_YEAR_AGO.isoformat()
    to = TODAY.isoformat()

    for i, symbol in enumerate(pending):
        try:
            articles = adapter.fetch(symbol, _from=_from, to=to)
            if articles:
                raw.insert_news_articles(articles)
            done.add(symbol)
            if i % 50 == 0:
                progress["news"] = list(done)
                save_progress(progress)
                print(f"  news: {len(done)}/{len(SYMBOLS)} — {symbol}: {len(articles)} articles")
        except Exception as e:
            print(f"  news {symbol} error: {e}")
        time.sleep(CALL_DELAY)

    progress["news"] = list(done)
    save_progress(progress)
    print(f"  news done: {len(done)} tickers")


def backfill_filings() -> None:
    """Finnhub SEC filings — 5 years."""
    print("\n=== FILINGS (finnhub) ===")
    if CLEAN:
        truncate_table("raw_filings")
        progress = load_progress()
        progress.pop("filings", None)
        save_progress(progress)

    adapter = FinnhubFilingsAdapter()
    progress = load_progress()
    done = set(progress.get("filings", []))
    pending = [s for s in SYMBOLS if s not in done]

    _from = FIVE_YEARS_AGO.isoformat()
    to = TODAY.isoformat()

    for i, symbol in enumerate(pending):
        try:
            filings = adapter.fetch_filings([symbol], _from=_from, to=to, forms=())
            if filings:
                raw.insert_filings(filings)
            done.add(symbol)
            if i % 50 == 0:
                progress["filings"] = list(done)
                save_progress(progress)
                print(f"  filings: {len(done)}/{len(SYMBOLS)} — {symbol}: {len(filings)} filings")
        except Exception as e:
            print(f"  filings {symbol} error: {e}")
        time.sleep(CALL_DELAY)

    progress["filings"] = list(done)
    save_progress(progress)
    print(f"  filings done: {len(done)} tickers")


def backfill_financials() -> None:
    """Finnhub financials as reported — 5 years."""
    print("\n=== FINANCIALS (finnhub) ===")
    if CLEAN:
        truncate_table("raw_financial_reports")
        progress = load_progress()
        progress.pop("financials", None)
        save_progress(progress)

    adapter = FinnhubFinancialsAdapter()
    progress = load_progress()
    done = set(progress.get("financials", []))
    pending = [s for s in SYMBOLS if s not in done]

    for i, symbol in enumerate(pending):
        try:
            reports = adapter.fetch([symbol], freq="quarterly")
            if reports:
                _insert_financial_reports(reports)
            done.add(symbol)
            if i % 50 == 0:
                progress["financials"] = list(done)
                save_progress(progress)
                print(f"  financials: {len(done)}/{len(SYMBOLS)} — {symbol}: {len(reports)} reports")
        except Exception as e:
            print(f"  financials {symbol} error: {e}")
        time.sleep(CALL_DELAY)

    progress["financials"] = list(done)
    save_progress(progress)
    print(f"  financials done: {len(done)} tickers")


def backfill_earnings() -> None:
    """Finnhub earnings surprises — free tier: last 4 quarters."""
    print("\n=== EARNINGS (finnhub) ===")
    if CLEAN:
        truncate_table("raw_earnings_surprises")
        progress = load_progress()
        progress.pop("earnings", None)
        save_progress(progress)

    adapter = FinnhubEarningsAdapter()
    progress = load_progress()
    done = set(progress.get("earnings", []))
    pending = [s for s in SYMBOLS if s not in done]

    for i, symbol in enumerate(pending):
        try:
            surprises = adapter.fetch([symbol], limit=20)  # max we can get
            if surprises:
                _insert_earnings_surprises(surprises)
            done.add(symbol)
            if i % 50 == 0:
                progress["earnings"] = list(done)
                save_progress(progress)
                print(f"  earnings: {len(done)}/{len(SYMBOLS)} — {symbol}: {len(surprises)} quarters")
        except Exception as e:
            print(f"  earnings {symbol} error: {e}")
        time.sleep(CALL_DELAY)

    progress["earnings"] = list(done)
    save_progress(progress)
    print(f"  earnings done: {len(done)} tickers")


def backfill_earnings_calendar() -> None:
    """Finnhub earnings calendar — free tier: 1 month. Fetch by month chunks."""
    print("\n=== EARNINGS CALENDAR (finnhub) ===")
    if CLEAN:
        truncate_table("raw_earnings_calendar")
        progress = load_progress()
        progress.pop("earncal_months", None)
        save_progress(progress)

    adapter = FinnhubEarningsCalendarAdapter()
    progress = load_progress()
    done_months = set(progress.get("earncal_months", []))

    # Fetch month by month for 5 years
    current = FIVE_YEARS_AGO
    while current < TODAY:
        month_key = current.strftime("%Y-%m")
        if month_key in done_months:
            current = (current.replace(day=1) + timedelta(days=32)).replace(day=1)
            continue

        month_end = (current.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        _from = current.isoformat()
        to = min(month_end, TODAY).isoformat()

        try:
            events = adapter.fetch(_from=_from, to=to)
            if events:
                _insert_earnings_calendar(events)
            done_months.add(month_key)
            progress["earncal_months"] = list(done_months)
            save_progress(progress)
            print(f"  earncal: {month_key} — {len(events)} events")
        except Exception as e:
            print(f"  earncal {month_key} error: {e}")

        current = (current.replace(day=1) + timedelta(days=32)).replace(day=1)
        time.sleep(CALL_DELAY)

    print(f"  earncal done: {len(done_months)} months")


def backfill_visa() -> None:
    """Finnhub visa applications — 5 years."""
    print("\n=== VISA (finnhub) ===")
    if CLEAN:
        truncate_table("raw_visa_applications")
        progress = load_progress()
        progress.pop("visa", None)
        save_progress(progress)

    adapter = FinnhubVisaAdapter()
    progress = load_progress()
    done = set(progress.get("visa", []))
    pending = [s for s in SYMBOLS if s not in done]

    _from = FIVE_YEARS_AGO.isoformat()
    to = TODAY.isoformat()

    for i, symbol in enumerate(pending):
        try:
            apps = adapter.fetch([symbol], _from=_from, to=to)
            if apps:
                _insert_visa_applications(apps)
            done.add(symbol)
            if i % 50 == 0:
                progress["visa"] = list(done)
                save_progress(progress)
                print(f"  visa: {len(done)}/{len(SYMBOLS)} — {symbol}: {len(apps)} applications")
        except Exception as e:
            print(f"  visa {symbol} error: {e}")
        time.sleep(CALL_DELAY)

    progress["visa"] = list(done)
    save_progress(progress)
    print(f"  visa done: {len(done)} tickers")


# ---- Raw insert helpers ----

def _insert_financial_reports(reports) -> int:
    if not reports:
        return 0
    client = get_client()
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
            "valid_from": (r.filed_date or date.today()).isoformat(),
        }
        for r in reports
    ]
    client.table("raw_financial_reports").upsert(
        rows, on_conflict="source,ticker,access_number", ignore_duplicates=True
    ).execute()
    return len(rows)


def _insert_earnings_surprises(surprises) -> int:
    if not surprises:
        return 0
    client = get_client()
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
        }
        for s in surprises
    ]
    client.table("raw_earnings_surprises").upsert(
        rows, on_conflict="source,ticker,period", ignore_duplicates=True
    ).execute()
    return len(rows)


def _insert_earnings_calendar(events) -> int:
    if not events:
        return 0
    client = get_client()
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
        }
        for e in events
    ]
    client.table("raw_earnings_calendar").upsert(
        rows, on_conflict="source,ticker,event_date,quarter", ignore_duplicates=True
    ).execute()
    return len(rows)


def _insert_visa_applications(apps) -> int:
    if not apps:
        return 0
    client = get_client()
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
            "valid_from": (a.received_date or date.today()).isoformat(),
        }
        for a in apps
    ]
    client.table("raw_visa_applications").upsert(
        rows, on_conflict="source,ticker,case_number", ignore_duplicates=True
    ).execute()
    return len(rows)


# ---- Main ----

ADAPTERS = {
    "price": backfill_prices,
    "news": backfill_news,
    "filings": backfill_filings,
    "financials": backfill_financials,
    "earnings": backfill_earnings,
    "earncal": backfill_earnings_calendar,
    "visa": backfill_visa,
}


def main() -> None:
    global CLEAN

    parser = argparse.ArgumentParser(description="Backfill all raw tables")
    parser.add_argument(
        "--adapter",
        choices=list(ADAPTERS) + ["all"],
        default="all",
        help="Which adapter to backfill (default: all)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Truncate target tables and reset progress before backfilling",
    )
    args = parser.parse_args()

    CLEAN = args.clean

    print(f"Curated universe: {len(SYMBOLS)} tickers")
    print(f"Rate limit: {60 / CALL_DELAY:.0f} calls/min")
    if CLEAN:
        print("CLEAN mode: tables will be truncated before backfill")

    if args.adapter == "all":
        for name, fn in ADAPTERS.items():
            fn()
    else:
        ADAPTERS[args.adapter]()

    print("\n=== BACKFILL COMPLETE ===")


if __name__ == "__main__":
    main()