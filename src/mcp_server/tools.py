"""MCP tool implementations (plan §10). Thin layer over the platform core.

Docstrings become the MCP tool descriptions — they are the agent-facing
catalog, so keep them precise. Drill-down tools compute live from the curated
zone (point-in-time correct via as_of); packages are the frozen daily digest.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from src.feeds import builder
from src.screener.engine import run_screener as _evaluate_screener
from src.storage import curated

_BAR_COLS = ("bar_date", "open", "high", "low", "close", "volume")
_IND_COLS = (
    "bar_date", "sma_10", "sma_20", "sma_50", "sma_200",
    "ema_10", "ema_20", "ema_50", "ema_200",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "atr_14", "bb_upper", "bb_lower", "volume_ma_20", "rel_strength_21d",
)


def _parse_as_of(as_of: str | None) -> date | None:
    return date.fromisoformat(as_of) if as_of else None


def _pick(rows: list[dict], cols: tuple[str, ...]) -> list[dict]:
    return [{k: (str(v) if k.endswith("date") else v) for k, v in r.items() if k in cols} for r in rows]


def get_research_package(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Full digest for a ticker: indicator summary, key ratios, screener status,
    data-quality flags, and cost context. Frozen daily snapshot; use as_of
    (YYYY-MM-DD) for the package as it looked on a past date."""
    pkg = curated.get_latest_package(ticker, feed="full", as_of=_parse_as_of(as_of))
    if pkg is None:
        return {"error": f"no research package for {ticker}"}
    return {
        "snapshot_id": pkg["snapshot_id"],
        "data_as_of": str(pkg["data_as_of"]),
        **pkg["payload_json"],
    }


def get_feed(ticker: str, feed: str, as_of: str | None = None) -> dict[str, Any]:
    """One role-scoped feed by name (price_technicals | fundamentals |
    news_sentiment | screener | data_quality | full). Every response carries
    its snapshot_id for audit."""
    pkg = curated.get_latest_package(ticker, feed=feed, as_of=_parse_as_of(as_of))
    if pkg is None:
        return {"error": f"no feed '{feed}' for {ticker}"}
    return {
        "snapshot_id": pkg["snapshot_id"],
        "feed": feed,
        "data_as_of": str(pkg["data_as_of"]),
        "payload": pkg["payload_json"],
    }


def get_price_history(ticker: str, window_days: int = 120, as_of: str | None = None) -> dict[str, Any]:
    """Drill-down: daily bars + indicator series for a ticker (adjusted at read
    time would apply in the feed; here bars are RAW as stored). window_days
    caps the lookback (default 120 trading days)."""
    rows = curated.get_price_history(ticker, window_days=window_days, as_of=_parse_as_of(as_of))
    inds = curated.get_indicators(ticker, window_days=window_days, as_of=_parse_as_of(as_of))
    return {
        "ticker": ticker,
        "bars": _pick(rows, _BAR_COLS),
        "indicators": _pick(inds, _IND_COLS),
    }


def get_fundamentals(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Drill-down: quarterly fundamentals (last 8 quarters) + derived ratios
    (P/E TTM, debt/equity, margins, growth), computed point-in-time."""
    return builder.build_fundamentals(ticker, _parse_as_of(as_of)) or {
        "error": f"no fundamentals for {ticker}"
    }


def run_screener(screen_name: str | None = None) -> dict[str, Any]:
    """Ad-hoc: evaluate screens over the current universe and return matches.
    Does not persist results (the daily job owns the record). Optionally
    restrict to one screen by name."""
    results = _evaluate_screener(date.today())
    matched = [r for r in results if r["matched"]]
    if screen_name:
        matched = [r for r in matched if r["screen_name"] == screen_name]
    return {"date": date.today().isoformat(), "matched": matched}


def get_screener_results(result_date: str | None = None) -> dict[str, Any]:
    """Persisted screener output: candidates with matched filters and metric
    values. Defaults to the most recent result date."""
    d = _parse_as_of(result_date)
    rows = curated.get_screener_results(result_date=d, matched_only=True)
    if not d and rows:
        latest = max(r["result_date"] for r in rows)
        rows = [r for r in rows if r["result_date"] == latest]
    return {
        "results": [
            {
                "date": str(r["result_date"]),
                "ticker": r["ticker"],
                "screen": r["screen_name"],
                "metrics": r["metrics_json"],
            }
            for r in rows
        ]
    }


def list_tickers(tier: str | None = None) -> dict[str, Any]:
    """Current tracked universe, optionally filtered by tier
    ('screening' | 'analysis')."""
    rows = curated.enabled_tickers(tier=tier)
    return {
        "tickers": [
            {
                "symbol": r["ticker"],
                "name": r.get("name"),
                "exchange": r.get("exchange"),
                "currency": r.get("currency"),
                "sector": r.get("sector"),
                "tier": r.get("tier"),
            }
            for r in rows
        ]
    }


def search_news(
    ticker: str, query: str | None = None, top_k: int = 10, as_of: str | None = None
) -> dict[str, Any]:
    """Search recent news articles for a ticker. Returns headlines with sentiment
    scores, most recent first. Optionally filter by a text query (simple headline
    substring match until Qdrant semantic search is wired in Phase 2b)."""
    articles = curated.get_news_articles(
        ticker=ticker, window_days=30, as_of=_parse_as_of(as_of), limit=100
    )
    if query:
        q = query.lower()
        articles = [a for a in articles if q in (a.get("headline") or "").lower()]
    top = articles[:top_k]
    return {
        "ticker": ticker,
        "articles": [
            {
                "date": str(a["published_at"])[:10],
                "headline": a.get("headline"),
                "sentiment": a.get("sentiment_score"),
            }
            for a in top
        ],
    }


def get_news_sentiment(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Daily sentiment aggregates for a ticker (last 30 days): 1d, 3d, 7d rolling
    mean sentiment scores plus article counts."""
    rows = curated.get_news_sentiment(ticker, window_days=30, as_of=_parse_as_of(as_of))
    return {
        "ticker": ticker,
        "daily": [
            {
                "date": str(r["date"]),
                "sent_1d": r.get("sent_1d"),
                "sent_3d": r.get("sent_3d"),
                "sent_7d": r.get("sent_7d"),
                "article_count": r.get("article_count"),
            }
            for r in rows
        ],
    }


def get_pipeline_runs(limit: int = 20) -> dict[str, Any]:
    """Recent pipeline runs: job, duration window, event and error counts.
    Use this to answer 'what ran / what failed lately?'"""
    return {"runs": curated.get_pipeline_runs(limit=limit)}


def get_pipeline_events(
    run_id: str | None = None, level: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """Structured pipeline events, filterable by run_id and level
    (INFO | WARN | ERROR). Use for 'why did X happen last night?'"""
    return {"events": curated.get_pipeline_events(run_id=run_id, level=level, limit=limit)}
