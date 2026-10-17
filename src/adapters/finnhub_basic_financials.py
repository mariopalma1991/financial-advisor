"""Finnhub basic financials adapter — valuation, margins, leverage and growth.

Finnhub's /stock/metric?metric=all returns, in one call per symbol:
- `metric`: the latest values (P/E TTM, P/B, margins TTM, ROE, debt/equity,
  revenue and EPS growth, beta, market cap...). Ratios such as margins, growth,
  ROE and dividend yield come in percent; we store them as fractions.
- `series.quarterly`: per-quarter history of ratios ({"period", "v"} lists), with
  margins already as fractions and Q4 included (derived from the 10-K by Finnhub).

We keep a small, named subset: every agent-facing number has a column, and the
rest of the payload (~130 metrics, decades of series) is dropped.

Rate limit: 30 calls/min on our Finnhub plan (shared across all Finnhub adapters).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import finnhub

from src.adapters.base import source_retry
from src.adapters.finnhub_visa import _finnhub_client

# column -> (candidate metric keys in order of preference, divide by 100?)
SNAPSHOT_METRICS: dict[str, tuple[tuple[str, ...], bool]] = {
    "market_cap": (("marketCapitalization",), False),  # millions of USD
    "beta": (("beta",), False),
    "pe_ttm": (("peTTM", "peExclExtraTTM", "peBasicExclExtraTTM"), False),
    "pb": (("pbQuarterly", "pb", "pbAnnual"), False),
    "ps_ttm": (("psTTM", "psAnnual"), False),
    "pfcf_ttm": (("pfcfShareTTM", "pfcfShareAnnual"), False),
    "dividend_yield": (("currentDividendYieldTTM", "dividendYieldIndicatedAnnual"), True),
    "eps_ttm": (("epsTTM", "epsExclExtraItemsTTM", "epsBasicExclExtraItemsTTM"), False),
    "eps_growth_ttm_yoy": (("epsGrowthTTMYoy",), True),
    "eps_growth_q_yoy": (("epsGrowthQuarterlyYoy",), True),
    "revenue_growth_ttm_yoy": (("revenueGrowthTTMYoy",), True),
    "revenue_growth_q_yoy": (("revenueGrowthQuarterlyYoy",), True),
    "revenue_growth_3y": (("revenueGrowth3Y",), True),
    "gross_margin_ttm": (("grossMarginTTM",), True),
    "operating_margin_ttm": (("operatingMarginTTM",), True),
    "net_margin_ttm": (("netProfitMarginTTM",), True),
    "roe_ttm": (("roeTTM", "roeRfy"), True),
    "roa_ttm": (("roaTTM", "roaRfy"), True),
    "debt_to_equity": (("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"), False),
    "current_ratio": (("currentRatioQuarterly", "currentRatioAnnual"), False),
    "high_52w": (("52WeekHigh",), False),
    "low_52w": (("52WeekLow",), False),
}

# column -> candidate series keys (already fractions / plain ratios)
QUARTERLY_SERIES: dict[str, tuple[str, ...]] = {
    "eps": ("eps",),
    "sales_per_share": ("salesPerShare",),
    "book_value_per_share": ("bookValue",),
    "gross_margin": ("grossMargin",),
    "operating_margin": ("operatingMargin",),
    "net_margin": ("netMargin",),
    "fcf_margin": ("fcfMargin",),
    "roe_ttm": ("roeTTM",),
    "roa_ttm": ("roaTTM",),
    "debt_to_equity": ("totalDebtToEquity",),
    "current_ratio": ("currentRatio",),
    "pe_ttm": ("peTTM", "pe"),
    "pb": ("pb",),
    "ps_ttm": ("psTTM", "ps"),
}

QUARTERS_KEPT = 12  # 3 years: enough for YoY comparisons and trend per agent


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


@dataclass
class BasicFinancials:
    ticker: str
    snapshot: dict[str, float | None]
    quarters: list[dict[str, Any]] = field(default_factory=list)


def parse(symbol: str, raw: dict[str, Any]) -> BasicFinancials | None:
    metric = (raw or {}).get("metric") or {}
    series = ((raw or {}).get("series") or {}).get("quarterly") or {}
    if not metric and not series:
        return None

    snapshot: dict[str, float | None] = {}
    for col, (keys, pct) in SNAPSHOT_METRICS.items():
        v = next((_num(metric.get(k)) for k in keys if _num(metric.get(k)) is not None), None)
        snapshot[col] = v / 100 if v is not None and pct else v

    by_period: dict[str, dict[str, Any]] = {}
    for col, keys in QUARTERLY_SERIES.items():
        points = next((series[k] for k in keys if series.get(k)), [])
        for p in points:
            period, v = p.get("period"), _num(p.get("v"))
            if period and v is not None:
                by_period.setdefault(period, {"period_end": period})[col] = v
    periods = sorted(by_period, reverse=True)[:QUARTERS_KEPT]
    quarters = [by_period[p] for p in sorted(periods)]

    # Revenue growth per share vs the quarter ending about a year earlier (fiscal
    # quarters can end on different days each year, hence the tolerance).
    ends = {q["period_end"]: date.fromisoformat(q["period_end"]) for q in quarters}
    for q in quarters:
        target = ends[q["period_end"]] - timedelta(days=365)
        prev = next((p for p in quarters if abs((ends[p["period_end"]] - target).days) <= 20), None)
        cur_s, prev_s = q.get("sales_per_share"), (prev or {}).get("sales_per_share")
        q["growth_yoy"] = (cur_s / prev_s - 1) if cur_s is not None and prev_s else None

    return BasicFinancials(ticker=symbol, snapshot=snapshot, quarters=quarters)


class FinnhubBasicFinancialsAdapter:
    """Fetches valuation and quality metrics from Finnhub /stock/metric.

    Usage:
        adapter = FinnhubBasicFinancialsAdapter()
        rows = adapter.fetch(["AAPL"])
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(self, symbol: str) -> dict[str, Any]:
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        return client.company_basic_financials(symbol, "all")

    def fetch(self, symbols: list[str], strict: bool = False) -> list[BasicFinancials]:
        out: list[BasicFinancials] = []
        for symbol in symbols:
            try:
                raw = self._fetch_one(symbol)
            except Exception:
                if strict:
                    raise
                continue
            parsed = parse(symbol, raw)
            if parsed:
                out.append(parsed)
        return out

