"""Curated zone writers and point-in-time queries.

Writers use change detection against the current_* views: a row is inserted only
when new or changed — jobs stay idempotent while full bitemporal history accrues.

Point-in-time (as_of) queries filter on valid_from <= as_of: what was *known in
the world* by then (bar close, filing date). After backfills, ingested_at is all
"now", so valid_from is the correct clock for historical simulation (plan §6).

All reads use the Supabase REST API (PostgREST over HTTPS) — no direct Postgres
connection needed. This avoids IPv6 connectivity issues from GitHub Actions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.adapters.base import RawCorporateAction, RawPriceBar
from src.config import load_analysis_universe, load_screening_universe
from src.storage.supabase_client import _exec, _paginate, get_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- REST query helpers ----------

def _current_map(view: str, key_cols: list[str], tickers: list[str]) -> dict[tuple, dict]:
    """Fetch current rows for given tickers from a view via REST."""
    if not tickers:
        return {}
    rows = _paginate(get_client().table(view).select("*").in_("ticker", tickers))
    return {tuple(str(r[c]) for c in key_cols): r for r in rows}


def _pit_rows(
    table: str,
    ticker: str,
    date_col: str,
    as_of: date | None,
    limit: int,
) -> list[dict]:
    """Point-in-time: latest version of each row known by as_of, ascending by date.

    Fetches all matching rows via REST, then deduplicates in Python keeping the
    row with the highest ingested_at per date_col (simulating DISTINCT ON).
    """
    query = get_client().table(table).select("*").eq("ticker", ticker)

    if as_of:
        query = query.lte(date_col, as_of.isoformat())
        as_of_ts = f"{as_of.isoformat()}T23:59:59+00:00"
        query = query.lte("valid_from", as_of_ts)

    query = query.order(date_col, desc=True).order("ingested_at", desc=True)
    rows = _paginate(query)

    # Dedup: keep first (latest ingested_at) per date_col
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        key = str(r.get(date_col, ""))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # Ascending by date, capped at limit
    deduped.sort(key=lambda r: str(r.get(date_col, "")))
    return deduped[-limit:] if limit < len(deduped) else deduped


# ---------- dim_ticker sync (config -> DB, versioned) ----------

def sync_dim_tickers() -> int:
    """Upsert both universes into dim_ticker; new version row only on change."""
    rows_in: list[dict[str, Any]] = []
    for tier, universe in (("analysis", load_analysis_universe()), ("screening", load_screening_universe())):
        for t in universe.tickers:
            rows_in.append(
                {
                    "ticker": t.symbol,
                    "name": t.name,
                    "exchange": t.exchange,
                    "currency": t.currency,
                    "sector": t.sector,
                    "benchmark": t.benchmark,
                    "tier": tier,
                    "enabled": t.enabled,
                    "tags": t.tags,
                    "notes": t.notes,
                }
            )
    current = _current_map("current_tickers", ["ticker"], [r["ticker"] for r in rows_in])
    now = _now()
    out = []
    for r in rows_in:
        cur = current.get((r["ticker"],))
        if cur and all(
            str(cur.get(k)) == str(v) for k, v in r.items() if k not in ("tags", "notes")
        ):
            continue
        out.append({**r, "valid_from": now, "ingested_at": now})
    if out:
        get_client().table("dim_ticker").insert(out).execute()
    return len(out)


# ---------- prices ----------

def _same_bar(cur: dict, b: RawPriceBar) -> bool:
    if b.close is None:
        return False
    try:
        return (
            abs(float(cur["close"]) - b.close) < 1e-9
            and (cur.get("volume") or None) == (b.volume or None)
        )
    except (TypeError, KeyError):
        return False


def upsert_price_bars(bars: list[RawPriceBar]) -> int:
    if not bars:
        return 0
    tickers = sorted({b.ticker for b in bars})
    current = _current_map("current_price_bars", ["ticker", "bar_date"], tickers)
    now = _now()
    rows = []
    for b in bars:
        cur = current.get((b.ticker, b.bar_date.isoformat()))
        if cur and _same_bar(cur, b):
            continue
        rows.append(
            {
                "ticker": b.ticker,
                "bar_date": b.bar_date.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "source_used": b.source,
                "valid_from": (b.valid_from or datetime.now(timezone.utc)).isoformat(),
                "ingested_at": now,
            }
        )
    if rows:
        get_client().table("price_bars_daily").insert(rows).execute()
    return len(rows)


def upsert_corporate_actions(actions: list[RawCorporateAction]) -> int:
    if not actions:
        return 0
    tickers = sorted({a.ticker for a in actions})
    current = _current_map("current_corporate_actions", ["ticker", "ex_date", "action_type"], tickers)
    now = _now()
    rows = []
    for a in actions:
        if (a.ticker, a.ex_date.isoformat(), a.action_type) in current:
            continue
        rows.append(
            {
                "ticker": a.ticker,
                "ex_date": a.ex_date.isoformat(),
                "action_type": a.action_type,
                "amount": a.amount,
                "ratio": a.ratio,
                "valid_from": f"{a.ex_date.isoformat()}T00:00:00+00:00",
                "ingested_at": now,
            }
        )
    if rows:
        get_client().table("corporate_actions").insert(rows).execute()
    return len(rows)


def upsert_indicators(rows: list[dict[str, Any]]) -> int:
    """Indicators are rebuildable derived data: upsert-ignore on (ticker, bar_date)."""
    if not rows:
        return 0
    now = _now()
    payload = [
        {**r, "valid_from": f"{r['bar_date']}T23:59:59+00:00", "ingested_at": now}
        for r in rows
    ]
    get_client().table("indicators_daily").upsert(
        payload, on_conflict="ticker,bar_date", ignore_duplicates=True
    ).execute()
    return len(payload)


# ---------- fundamentals ----------

_FUND_FIELDS = ("revenue", "net_income", "eps", "total_debt", "equity", "gross_margin", "net_margin", "growth_yoy")


def _same_fundamental(cur: dict, r: dict) -> bool:
    """None-aware field comparison: filling a previously-null field IS a change."""
    for k in _FUND_FIELDS:
        a, b = cur.get(k), r.get(k)
        if a is None and b is None:
            continue
        if a is None or b is None:
            return False
        if abs(float(a) - float(b)) > 1e-6:
            return False
    return True


def upsert_fundamentals(rows: list[dict[str, Any]]) -> int:
    """Rows come from transforms/fundamentals.py (quarterly, one per metric set)."""
    if not rows:
        return 0
    tickers = sorted({r["ticker"] for r in rows})
    current = _current_map("current_fundamentals", ["ticker", "period_end"], tickers)
    now = _now()
    out = []
    for r in rows:
        key = (r["ticker"], str(r["period_end"]))
        cur = current.get(key)
        if cur and _same_fundamental(cur, r):
            continue
        out.append({**r, "ingested_at": now})
    if out:
        get_client().table("fundamentals_quarterly").insert(out).execute()
    return len(out)


# ---------- screener & feeds ----------

def insert_screener_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    now = _now()
    payload = [{**r, "valid_from": now, "ingested_at": now} for r in rows]
    get_client().table("screener_results").upsert(
        payload, on_conflict="result_date,ticker,screen_name", ignore_duplicates=True
    ).execute()
    return len(payload)


def insert_research_packages(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    get_client().table("research_packages").upsert(
        rows, on_conflict="ticker,snapshot_id,feed", ignore_duplicates=True
    ).execute()
    return len(rows)


# ---------- point-in-time reads (as_of) ----------

def get_price_history(ticker: str, window_days: int | None = None, as_of: date | None = None) -> list[dict]:
    return _pit_rows("price_bars_daily", ticker, "bar_date", as_of, window_days or 10_000)


def get_indicators(ticker: str, window_days: int | None = None, as_of: date | None = None) -> list[dict]:
    return _pit_rows("indicators_daily", ticker, "bar_date", as_of, window_days or 10_000)


def get_fundamentals(ticker: str, quarters: int = 8, as_of: date | None = None) -> list[dict]:
    return _pit_rows("fundamentals_quarterly", ticker, "period_end", as_of, quarters)


def get_corporate_actions(ticker: str, as_of: date | None = None) -> list[dict]:
    return _pit_rows("corporate_actions", ticker, "ex_date", as_of, 10_000)


def get_screener_results(result_date: date | None = None, matched_only: bool = True) -> list[dict]:
    query = get_client().table("screener_results").select("*")
    if result_date:
        query = query.eq("result_date", result_date.isoformat())
    if matched_only:
        query = query.eq("matched", True)
    query = query.order("result_date", desc=True).order("screen_name").order("ticker")
    return _paginate(query)


def get_latest_package(ticker: str, feed: str = "full", as_of: date | None = None) -> dict | None:
    query = (
        get_client()
        .table("research_packages")
        .select("*")
        .eq("ticker", ticker)
        .eq("feed", feed)
        .order("data_as_of", desc=True)
        .order("generated_at", desc=True)
        .limit(1)
    )
    if as_of:
        query = query.lte("data_as_of", as_of.isoformat())
    rows = _exec(query)
    return rows[0] if rows else None


def get_pipeline_runs(limit: int = 20) -> list[dict]:
    """Recent pipeline runs with event/error counts, computed in Python."""
    rows = _paginate(
        get_client()
        .table("pipeline_events")
        .select("*")
        .order("created_at", desc=True)
    )
    # Group by (run_id, job_name) and compute aggregates
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["run_id"], r["job_name"])
        if key not in groups:
            groups[key] = {
                "run_id": r["run_id"],
                "job_name": r["job_name"],
                "started_at": r["created_at"],
                "finished_at": r["created_at"],
                "errors": 0,
                "events": 0,
            }
        g = groups[key]
        g["events"] += 1
        if r.get("level") == "ERROR":
            g["errors"] += 1
        if r["created_at"] < g["started_at"]:
            g["started_at"] = r["created_at"]
        if r["created_at"] > g["finished_at"]:
            g["finished_at"] = r["created_at"]

    result = sorted(groups.values(), key=lambda g: g["finished_at"], reverse=True)
    return result[:limit]


def get_pipeline_events(
    run_id: str | None = None, level: str | None = None, limit: int = 100
) -> list[dict]:
    query = get_client().table("pipeline_events").select("*")
    if run_id:
        query = query.eq("run_id", run_id)
    if level:
        query = query.eq("level", level)
    query = query.order("created_at", desc=True).limit(limit)
    return _exec(query)


def get_latest_bars_all() -> dict[str, dict]:
    """Latest price bar per ticker (dedup in Python)."""
    rows = _paginate(
        get_client()
        .table("price_bars_daily")
        .select("*")
        .order("ticker")
        .order("bar_date", desc=True)
        .order("ingested_at", desc=True)
    )
    seen: set[str] = set()
    result: dict[str, dict] = {}
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            result[r["ticker"]] = r
    return result


def get_latest_indicators_all() -> dict[str, dict]:
    """Latest indicator row per ticker (dedup in Python)."""
    rows = _paginate(
        get_client()
        .table("indicators_daily")
        .select("*")
        .order("ticker")
        .order("bar_date", desc=True)
        .order("ingested_at", desc=True)
    )
    seen: set[str] = set()
    result: dict[str, dict] = {}
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            result[r["ticker"]] = r
    return result


def get_52w_highs_all() -> dict[str, float]:
    """52-week high per ticker, computed in Python."""
    one_year_ago = (date.today() - timedelta(days=365)).isoformat()
    rows = _paginate(
        get_client()
        .table("price_bars_daily")
        .select("ticker,high")
        .gte("bar_date", one_year_ago)
    )
    highs: dict[str, float] = {}
    for r in rows:
        t = r["ticker"]
        h = r.get("high")
        if h is not None:
            highs[t] = max(highs.get(t, float("-inf")), float(h))
    return highs


def get_fundamentals_all(quarters: int = 5) -> dict[str, list[dict]]:
    """Recent fundamentals per ticker (ascending by period)."""
    rows = _paginate(
        get_client()
        .table("current_fundamentals")
        .select("*")
        .order("ticker")
        .order("period_end", desc=True)
    )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["ticker"]].append(r)
    return {t: list(reversed(rs[:quarters])) for t, rs in grouped.items()}


def enabled_tickers(tier: str | None = None) -> list[dict]:
    query = get_client().table("current_tickers").select("*").eq("enabled", True)
    if tier:
        query = query.eq("tier", tier)
    return _paginate(query)


# ---------- news ----------

def upsert_news_articles(rows: list[dict[str, Any]]) -> int:
    """Insert curated news articles; idempotent on article_id."""
    if not rows:
        return 0
    now = _now()
    payload = [{**r, "ingested_at": now} for r in rows]
    get_client().table("news_articles").upsert(
        payload, on_conflict="article_id", ignore_duplicates=True
    ).execute()
    return len(payload)


def upsert_news_sentiment(rows: list[dict[str, Any]]) -> int:
    """Upsert daily sentiment aggregates; rebuildable derived data."""
    if not rows:
        return 0
    now = _now()
    payload = [
        {**r, "valid_from": f"{r['date']}T23:59:59+00:00", "ingested_at": now}
        for r in rows
    ]
    get_client().table("news_sentiment_daily").upsert(
        payload, on_conflict="ticker,date", ignore_duplicates=True
    ).execute()
    return len(payload)


def get_news_articles(
    ticker: str | None = None,
    window_days: int = 30,
    as_of: date | None = None,
    limit: int = 100,
) -> list[dict]:
    """Point-in-time news articles, most recent first."""
    query = get_client().table("news_articles").select("*")

    if ticker:
        # PostgREST filter for array containment: cs = any(tickers)
        # We use a contains filter on the tickers array
        query = query.contains("tickers", [ticker])

    if as_of:
        query = query.lte("valid_from", f"{as_of.isoformat()}T23:59:59+00:00")

    if window_days:
        cutoff = (date.today() - timedelta(days=window_days)).isoformat()
        query = query.gte("published_at", cutoff)

    query = query.order("published_at", desc=True).limit(limit)
    return _exec(query)


def get_news_sentiment(
    ticker: str,
    window_days: int = 30,
    as_of: date | None = None,
) -> list[dict]:
    """Point-in-time daily sentiment aggregates, ascending by date."""
    query = get_client().table("news_sentiment_daily").select("*").eq("ticker", ticker)

    if as_of:
        query = query.lte("valid_from", f"{as_of.isoformat()}T23:59:59+00:00")

    if window_days:
        cutoff = (date.today() - timedelta(days=window_days)).isoformat()
        query = query.gte("date", cutoff)

    query = query.order("date", desc=False)
    rows = _paginate(query)
    return rows
