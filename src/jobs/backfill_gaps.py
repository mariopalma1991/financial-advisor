"""Backfill data missed by failed or timed-out pipeline runs.

For each source it finds where the stored data stops, then re-runs that
pipeline over the whole universe from there (with a small overlap). Every
pipeline is idempotent, so re-fetching days that are already stored only
costs time — nothing is duplicated.

    python -m src.jobs.backfill_gaps                       # detect gaps, fill all
    python -m src.jobs.backfill_gaps --dry-run             # just show the gaps
    python -m src.jobs.backfill_gaps --sources news,filings
    python -m src.jobs.backfill_gaps --since 2026-10-11    # explicit start date

Gap detection (latest date already stored):
    prices   current_price_bars.bar_date
    news     raw_news_articles.published_at (source = finnhub)
    filings  raw_filings.filed_at (source = finnhub)

Sources run one after another (news and filings share Finnhub's 60 calls/min).
Each gets its own job run in pipeline_events; one failing does not stop the
others, but the script exits non-zero.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

from src.jobs import ingest_filings, ingest_news, ingest_prices
from src.quality.run_metadata import job_run
from src.storage.supabase_client import _exec, get_client

SOURCES = ("prices", "news", "filings")

# (view/table, date column, extra equality filters)
_LATEST_QUERY = {
    "prices": ("current_price_bars", "bar_date", {}),
    "news": ("raw_news_articles", "published_at", {"source": "finnhub"}),
    "filings": ("raw_filings", "filed_at", {"source": "finnhub"}),
}

# Tables each pipeline writes to — checked by --dry-run so a missing table is
# reported up front instead of failing after a long fetch.
_WRITE_TABLES = {
    "prices": ["raw_price_bars", "raw_corporate_actions", "price_bars_daily"],
    "news": ["raw_news_articles", "news_articles", "news_sentiment_daily"],
    "filings": ["raw_filings"],
}


def missing_tables(source: str) -> list[str]:
    missing = []
    for table in _WRITE_TABLES[source]:
        try:
            _exec(get_client().table(table).select("*").limit(1))
        except Exception as exc:
            missing.append(f"{table} ({type(exc).__name__}: {str(exc)[:150]})")
    return missing


# Re-fetch this many days before the latest stored date, to catch late
# corrections, late-published articles and filings indexed after the fact.
_OVERLAP_DAYS = {"prices": 1, "news": 1, "filings": 3}


def latest_stored(source: str) -> date | None:
    table, col, filters = _LATEST_QUERY[source]
    query = get_client().table(table).select(col)
    for k, v in filters.items():
        query = query.eq(k, v)
    rows = _exec(query.order(col, desc=True).limit(1))
    if not rows or rows[0].get(col) is None:
        return None
    value = str(rows[0][col])
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def gap_start(source: str, today: date, max_days: int, since: date | None) -> tuple[date, str]:
    """First day to (re)fetch for `source`, and a note on how it was chosen."""
    floor = today - timedelta(days=max_days)
    if since is not None:
        return max(since, floor), "explicit --since"
    latest = latest_stored(source)
    if latest is None:
        return floor, f"no data stored; using --max-days {max_days}"
    start = latest - timedelta(days=_OVERLAP_DAYS[source])
    if start < floor:
        return floor, f"latest stored {latest}; capped at --max-days {max_days}"
    return start, f"latest stored {latest}"


def _run_source(source: str, start: date, budget_s: float) -> None:
    with job_run(f"backfill_{source}") as em:
        em.emit("INFO", "backfill_window", f"{source} from {start}", payload={"from": start.isoformat()})
        if source == "prices":
            ingest_prices.run(em, start)
        elif source == "news":
            # Fetch 6 extra days so sentiment aggregates from `start` on have
            # complete 7-day windows (see ingest_news.run).
            _from = (start - timedelta(days=6)).isoformat()
            ingest_news.run(em, _from, max_symbols=None, budget_s=budget_s)
        elif source == "filings":
            ingest_filings.run(em, start.isoformat(), max_symbols=None, budget_s=budget_s)


def _notice(msg: str) -> None:
    """Print, and in GitHub Actions also surface as a run annotation."""
    print(msg, flush=True)
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::notice title=backfill_gaps::{msg}", flush=True)


def _error(msg: str) -> None:
    print(f"!!! {msg}", flush=True)
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::error title=backfill_gaps::{msg[:3000]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--sources",
        default=",".join(SOURCES),
        help=f"comma-separated subset of {', '.join(SOURCES)} (default: all)",
    )
    parser.add_argument("--since", type=date.fromisoformat, help="start date YYYY-MM-DD (skips detection)")
    parser.add_argument(
        "--max-days", type=int, default=30, help="never reach back further than this (default 30)"
    )
    parser.add_argument(
        "--budget-min",
        type=float,
        default=100,
        help="time budget per Finnhub source, minutes (default 100)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show detected windows and exit")
    args = parser.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")

    today = date.today()
    plan: list[tuple[str, date]] = []
    failed: list[str] = []
    for source in sources:
        try:
            start, note = gap_start(source, today, args.max_days, args.since)
        except Exception as exc:
            _error(f"{source}: gap detection failed — {type(exc).__name__}: {exc}")
            failed.append(source)
            continue
        _notice(f"{source:8} from {start} ({(today - start).days} days)  — {note}")
        plan.append((source, start))

    if args.dry_run:
        for source in sources:
            for m in missing_tables(source):
                _error(f"{source}: cannot read table {m}")
                if source not in failed:
                    failed.append(source)
        return 1 if failed else 0

    for source, start in plan:
        print(f"\n=== backfill {source} from {start}", flush=True)
        try:
            _run_source(source, start, args.budget_min * 60)
        except Exception as exc:  # job_run already logged + alerted
            _error(f"{source} failed: {type(exc).__name__}: {exc}")
            failed.append(source)

    if failed:
        _notice(f"Failed: {', '.join(failed)}")
        return 1
    _notice("All backfills completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
