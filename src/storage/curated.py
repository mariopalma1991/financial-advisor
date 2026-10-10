"""Curated zone writers and point-in-time queries.

Writers use change detection against the current_* views: a row is inserted only
when new or changed — jobs stay idempotent while full bitemporal history accrues.

Point-in-time (as_of) queries filter on valid_from <= as_of: what was *known in
the world* by then (bar close, filing date). After backfills, ingested_at is all
"now", so valid_from is the correct clock for historical simulation (plan §6).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from src.adapters.base import RawCorporateAction, RawPriceBar
from src.config import load_analysis_universe, load_screening_universe
from src.storage.supabase_client import fetch_all, get_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_map(view: str, key_cols: list[str], tickers: list[str]) -> dict[tuple, dict]:
    if not tickers:
        return {}
    rows = fetch_all(f"select * from {view} where ticker = any(%s)", (tickers,))
    return {tuple(str(r[c]) for c in key_cols): r for r in rows}


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

def _pit_query(table: str, ticker: str, date_col: str, as_of: date | None, limit: int) -> list[dict]:
    """Latest version of each row, known in the world by as_of, most recent first."""
    as_of_ts = f"{as_of.isoformat()}T23:59:59+00:00" if as_of else None
    sql = f"""
        select * from (
            select distinct on ({date_col}) *
            from {table}
            where ticker = %s
              and (%s::date is null or {date_col} <= %s)
              and (%s::timestamptz is null or valid_from <= %s)
            order by {date_col}, ingested_at desc
        ) t
        order by {date_col} desc
        limit %s
    """
    rows = fetch_all(sql, (ticker, as_of, as_of, as_of_ts, as_of_ts, limit))
    rows.reverse()  # ascending by date
    return rows


def get_price_history(ticker: str, window_days: int | None = None, as_of: date | None = None) -> list[dict]:
    return _pit_query("price_bars_daily", ticker, "bar_date", as_of, window_days or 10_000)


def get_indicators(ticker: str, window_days: int | None = None, as_of: date | None = None) -> list[dict]:
    return _pit_query("indicators_daily", ticker, "bar_date", as_of, window_days or 10_000)


def get_fundamentals(ticker: str, quarters: int = 8, as_of: date | None = None) -> list[dict]:
    return _pit_query("fundamentals_quarterly", ticker, "period_end", as_of, quarters)


def get_corporate_actions(ticker: str, as_of: date | None = None) -> list[dict]:
    return _pit_query("corporate_actions", ticker, "ex_date", as_of, 10_000)


def get_screener_results(result_date: date | None = None, matched_only: bool = True) -> list[dict]:
    sql = "select * from screener_results"
    params: list[Any] = []
    clauses: list[str] = []
    if result_date:
        clauses.append("result_date = %s")
        params.append(result_date)
    if matched_only:
        clauses.append("matched = true")
    if clauses:
        sql += " where " + " and ".join(clauses)
    sql += " order by result_date desc, screen_name, ticker"
    return fetch_all(sql, tuple(params))


def get_latest_package(ticker: str, feed: str = "full", as_of: date | None = None) -> dict | None:
    sql = """
        select * from research_packages
        where ticker = %s and feed = %s
          and (%s::date is null or data_as_of <= %s)
        order by data_as_of desc, generated_at desc
        limit 1
    """
    rows = fetch_all(sql, (ticker, feed, as_of, as_of))
    return rows[0] if rows else None


def get_pipeline_runs(limit: int = 20) -> list[dict]:
    return fetch_all(
        """
        select run_id, job_name,
               min(created_at) as started_at, max(created_at) as finished_at,
               count(*) filter (where level = 'ERROR') as errors,
               count(*) as events
        from pipeline_events
        group by run_id, job_name
        order by max(created_at) desc
        limit %s
        """,
        (limit,),
    )


def get_pipeline_events(
    run_id: str | None = None, level: str | None = None, limit: int = 100
) -> list[dict]:
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("run_id = %s")
        params.append(run_id)
    if level:
        clauses.append("level = %s")
        params.append(level)
    sql = "select * from pipeline_events"
    if clauses:
        sql += " where " + " and ".join(clauses)
    sql += " order by created_at desc limit %s"
    params.append(limit)
    return fetch_all(sql, tuple(params))


def get_latest_bars_all() -> dict[str, dict]:
    """Latest price bar per ticker, one query (screener bulk path)."""
    rows = fetch_all(
        "select distinct on (ticker) * from price_bars_daily "
        "order by ticker, bar_date desc, ingested_at desc"
    )
    return {r["ticker"]: r for r in rows}


def get_latest_indicators_all() -> dict[str, dict]:
    """Latest indicator row per ticker, one query."""
    rows = fetch_all(
        "select distinct on (ticker) * from indicators_daily "
        "order by ticker, bar_date desc, ingested_at desc"
    )
    return {r["ticker"]: r for r in rows}


def get_52w_highs_all() -> dict[str, float]:
    """52-week high per ticker, one query."""
    rows = fetch_all(
        "select ticker, max(high) as high_252 from price_bars_daily "
        "where bar_date >= current_date - interval '1 year' group by ticker"
    )
    return {r["ticker"]: r["high_252"] for r in rows}


def get_fundamentals_all(quarters: int = 5) -> dict[str, list[dict]]:
    """Recent fundamentals per ticker (ascending by period), one query."""
    rows = fetch_all("select * from current_fundamentals order by ticker, period_end desc")
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["ticker"], []).append(r)
    return {t: list(reversed(rs[:quarters])) for t, rs in grouped.items()}


def enabled_tickers(tier: str | None = None) -> list[dict]:
    sql = "select * from current_tickers where enabled = true"
    params: tuple = ()
    if tier:
        sql += " and tier = %s"
        params = (tier,)
    return fetch_all(sql, params)
