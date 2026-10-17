"""Feed Builder — assembles versioned, role-scoped feeds per analysis-tier ticker.

Every feed built in one run shares a snapshot_id, so the exact world-state each
agent saw is reconstructable (plan §8). Feeds are views, not pipelines: they are
assembled from the curated zone at build time, with prices adjusted as of as_of.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from src.config import load_cost_model, load_platform
from src.storage import curated
from src.transforms.adjustments import adjust_bars
from src.transforms.insider_sentiment import summarize as summarize_insider_sentiment

SCHEMA_VERSION = "1.0"


def _r(x: Any, nd: int = 4) -> float | None:
    try:
        return None if x is None or pd.isna(x) else round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _returns(bars: pd.DataFrame, windows: dict[str, int]) -> dict[str, float | None]:
    close = bars["close"]
    return {
        label: (round(float(close.iloc[-1] / close.iloc[-1 - w] - 1), 6) if len(close) > w else None)
        for label, w in windows.items()
    }


def _adjusted_bars(ticker: str, as_of: date | None, window_days: int = 300) -> pd.DataFrame:
    rows = curated.get_price_history(ticker, window_days=window_days, as_of=as_of)
    if not rows:
        return pd.DataFrame()
    bars = pd.DataFrame(rows)
    bars["bar_date"] = pd.to_datetime(bars["bar_date"]).dt.date
    bars = bars.set_index("bar_date").sort_index()
    actions_rows = curated.get_corporate_actions(ticker, as_of=as_of)
    actions = (
        pd.DataFrame(actions_rows)
        if actions_rows
        else pd.DataFrame(columns=["ex_date", "action_type", "amount", "ratio"])
    )
    if not actions.empty:
        actions["ex_date"] = pd.to_datetime(actions["ex_date"]).dt.date
    return adjust_bars(bars, actions, as_of=as_of)


def build_price_technicals(ticker: str, as_of: date | None = None) -> dict[str, Any]:
    adj = _adjusted_bars(ticker, as_of)
    if adj.empty:
        return {}
    inds = curated.get_indicators(ticker, window_days=1, as_of=as_of)
    latest_ind = inds[-1] if inds else {}
    windows = load_platform().horizons["returns_windows"]
    highs = [h for h in adj["high"].tail(252) if pd.notna(h)]
    lows = [l for l in adj["low"].tail(252) if pd.notna(l)]
    return {
        "last_close": _r(adj["close"].iloc[-1]),
        "returns": _returns(adj, windows),
        "indicators": {
            k: _r(latest_ind.get(k))
            for k in (
                "rsi_14", "macd", "macd_signal", "macd_hist",
                "sma_10", "sma_20", "sma_50", "sma_200",
                "ema_10", "ema_20", "ema_50", "ema_200",
                "atr_14", "bb_upper", "bb_lower", "volume_ma_20", "rel_strength_21d",
            )
        },
        "high_52w": _r(max(highs) if highs else None),
        "low_52w": _r(min(lows) if lows else None),
    }


def build_fundamentals(ticker: str, as_of: date | None = None) -> dict[str, Any]:
    """Valuation and quality: latest Finnhub snapshot + last 8 quarters of ratios."""
    snap = curated.get_fundamentals_snapshot(ticker, as_of=as_of)
    rows = curated.get_fundamentals(ticker, quarters=8, as_of=as_of)
    if not snap and not rows:
        return {}
    snap = snap or {}
    return {
        "as_of": str(snap["as_of"]) if snap.get("as_of") else None,
        "valuation": {k: _r(snap.get(k)) for k in ("pe_ttm", "pb", "ps_ttm", "pfcf_ttm", "dividend_yield")},
        "growth": {
            k: _r(snap.get(k))
            for k in ("revenue_growth_ttm_yoy", "revenue_growth_q_yoy", "revenue_growth_3y",
                      "eps_growth_ttm_yoy", "eps_growth_q_yoy")
        },
        "quality": {
            k: _r(snap.get(k))
            for k in ("gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm", "roe_ttm", "roa_ttm")
        },
        "leverage": {k: _r(snap.get(k)) for k in ("debt_to_equity", "current_ratio")},
        "market_cap_musd": _r(snap.get("market_cap"), 0),
        "beta": _r(snap.get("beta"), 2),
        "quarters": [
            {
                "period_end": str(r["period_end"]),
                **{k: _r(r.get(k)) for k in ("eps", "gross_margin", "operating_margin", "net_margin",
                                             "growth_yoy", "debt_to_equity", "pe_ttm")},
            }
            for r in rows
        ],
    }


def build_news_sentiment(ticker: str, as_of: date | None = None) -> dict[str, Any]:
    """News digest: sentiment windows + top headlines for one ticker."""
    articles = curated.get_news_articles(ticker=ticker, window_days=30, as_of=as_of, limit=50)
    sentiment_rows = curated.get_news_sentiment(ticker, window_days=30, as_of=as_of)

    # Latest sentiment windows
    latest_sent = sentiment_rows[-1] if sentiment_rows else {}
    sentiment = {
        "1d": _r(latest_sent.get("sent_1d")),
        "3d": _r(latest_sent.get("sent_3d")),
        "7d": _r(latest_sent.get("sent_7d")),
    }

    # Top headlines by recency × sentiment strength
    top_n = load_platform().feeds.get("top_news_items", 10)
    ranked = sorted(
        articles,
        key=lambda a: abs(a.get("sentiment_score") or 0),
        reverse=True,
    )[:top_n]

    top_items = [
        {
            "date": str(a["published_at"])[:10],
            "headline": a.get("headline"),
            "sentiment": _r(a.get("sentiment_score")),
        }
        for a in ranked
    ]

    return {
        "sentiment": sentiment,
        "article_count_7d": latest_sent.get("article_count", 0) if latest_sent else 0,
        "top_items": top_items,
    }


def build_insider_sentiment(ticker: str, as_of: date | None = None) -> dict[str, Any]:
    """Insider buying/selling pressure (Form 4, monthly): MSPR and net shares."""
    return summarize_insider_sentiment(curated.get_insider_sentiment(ticker, months=12, as_of=as_of))


def build_screener_feed(ticker: str) -> dict[str, Any]:
    rows = [r for r in curated.get_screener_results(matched_only=True) if r["ticker"] == ticker]
    if not rows:
        return {"matched_screens": []}
    latest_date = max(r["result_date"] for r in rows)
    matched = [r["screen_name"] for r in rows if r["result_date"] == latest_date]
    return {"as_of": str(latest_date), "matched_screens": matched}


def build_data_quality(ticker: str) -> dict[str, Any]:
    bars = curated.get_price_history(ticker, window_days=5)
    latest = bars[-1]["bar_date"] if bars else None
    age_days = (date.today() - latest).days if latest else None
    warnings = [
        e
        for e in curated.get_pipeline_events(level="WARN", limit=50)
        + curated.get_pipeline_events(level="ERROR", limit=50)
        if e.get("ticker") == ticker
    ][:10]
    return {
        "prices_fresh": (age_days is not None and age_days <= 5),
        "latest_bar_date": str(latest) if latest else None,
        "recent_warnings": [
            {"type": e["event_type"], "message": e.get("message"), "at": str(e["created_at"])}
            for e in warnings
        ],
    }


def build_full(ticker: str, feeds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    info = next((t for t in curated.enabled_tickers() if t["ticker"] == ticker), {})
    cost = load_cost_model()
    currency = info.get("currency", "USD")
    return {
        "ticker": ticker,
        "name": info.get("name"),
        "sector": info.get("sector"),
        "currency": currency,
        "price_summary": feeds.get("price_technicals", {}),
        "fundamentals": feeds.get("fundamentals", {}),
        "news_digest": feeds.get("news_sentiment", {}),
        "insider_sentiment": feeds.get("insider_sentiment", {}),
        "screener": feeds.get("screener", {}),
        "data_quality": feeds.get("data_quality", {}),
        "cost_context": {
            "round_trip_pct": round(cost.round_trip_pct(currency), 5),
            "min_edge_pct": round(cost.min_edge_pct(currency), 5),
        },
    }


def build_all(as_of: date | None = None) -> int:
    """Build all v1 feeds for every analysis-tier ticker; one shared snapshot_id."""
    now = datetime.now(timezone.utc)
    snapshot_id = now.strftime("%Y%m%dT%H%M%SZ")
    data_as_of = as_of or date.today()
    tickers = [t["ticker"] for t in curated.enabled_tickers(tier="analysis")]

    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        feeds = {
            "price_technicals": build_price_technicals(ticker, as_of),
            "fundamentals": build_fundamentals(ticker, as_of),
            "news_sentiment": build_news_sentiment(ticker, as_of),
            "insider_sentiment": build_insider_sentiment(ticker, as_of),
            "screener": build_screener_feed(ticker),
            "data_quality": build_data_quality(ticker),
        }
        feeds["full"] = build_full(ticker, feeds)
        for feed_name, payload in feeds.items():
            if not payload:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "snapshot_id": snapshot_id,
                    "feed": feed_name,
                    "generated_at": now.isoformat(),
                    "data_as_of": data_as_of.isoformat(),
                    "schema_version": SCHEMA_VERSION,
                    "payload_json": payload,
                    "ingested_at": now.isoformat(),
                }
            )
    return curated.insert_research_packages(rows)
