"""Shape raw EDGAR facts into quarterly fundamental rows (plan §7).

Point-in-time choice: for each (ticker, period, metric) we keep the value from
the *earliest* filing — what was first publicly known. Later restatements are
rare and, when they matter, arrive as new facts with later filed dates; the
curated layer's change detection versions them. (Documented v1 simplification.)

Two EDGAR realities handled here:
- Q4 gaps: Q4 flow values usually appear only in the 10-K as annual figures.
  We derive Q4 = FY - (Q1 + Q2 + Q3) when all parts are available. (Exact for
  revenue/net income; approximate for EPS due to share-count drift.)
- Fiscal quarters ending on Saturdays (e.g. Apple): YoY growth matches the
  nearest period_end one year earlier within a tolerance window.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

# NOTE: RawFundamentalFact was removed with edgar_filings.py.
# Fundamentals now come from FinnhubFinancialsAdapter (RawFinancialReport).
# The to_quarterly_rows() transform needs to be adapted for the new data model.
from src.adapters.base import RawFinancialReport

_YOY_TOLERANCE_DAYS = 20
_FLOW_METRICS = {"revenue", "net_income", "eps", "gross_profit"}


def _derive_q4(rows: list[dict[str, Any]], annuals: list[RawFundamentalFact]) -> None:
    """Fill missing Q4 flow values as FY - (Q1+Q2+Q3), in place."""
    by_ticker: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        by_ticker[r["ticker"]][r["period_end"]] = r

    for a in annuals:
        ticker_rows = by_ticker.get(a.ticker)
        if not ticker_rows:
            continue
        target = ticker_rows.get(a.period_end.isoformat())
        if target is None or target.get(a.metric) is not None:
            continue  # no Q4 row, or Q4 already has a value
        prior = sorted(
            (
                r
                for r in ticker_rows.values()
                if 0 < (a.period_end - date.fromisoformat(r["period_end"])).days <= 310
            ),
            key=lambda r: r["period_end"],
            reverse=True,
        )[:3]
        if len(prior) == 3 and all(r.get(a.metric) is not None for r in prior):
            target[a.metric] = a.value - sum(r[a.metric] for r in prior)
            target["q4_derived"] = True


def _fill_growth_yoy(rows: list[dict[str, Any]]) -> None:
    """Revenue growth vs the nearest period one year earlier (fiscal-quarter drift)."""
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    for ticker_rows in by_ticker.values():
        periods = [date.fromisoformat(r["period_end"]) for r in ticker_rows]
        rev = {r["period_end"]: r["revenue"] for r in ticker_rows}
        for r in ticker_rows:
            if r.get("revenue") is None:
                continue
            current = date.fromisoformat(r["period_end"])
            try:
                target = current.replace(year=current.year - 1)
            except ValueError:  # Feb 29
                continue
            candidates = [
                p for p in periods if abs((p - target).days) <= _YOY_TOLERANCE_DAYS
            ]
            if not candidates:
                continue
            nearest = min(candidates, key=lambda p: abs((p - target).days))
            prev_rev = rev.get(nearest.isoformat())
            if prev_rev:
                r["growth_yoy"] = r["revenue"] / prev_rev - 1


def to_quarterly_rows(facts: list[RawFundamentalFact]) -> list[dict[str, Any]]:
    quarterly: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    filed_at: dict[tuple[str, str], Any] = {}
    annuals: list[RawFundamentalFact] = []

    for f in facts:
        if f.payload.get("annual"):
            annuals.append(f)
            continue
        key = (f.ticker, f.period_end.isoformat())
        if f.metric not in quarterly[key]:
            quarterly[key][f.metric] = f.value
            filed_at.setdefault(key, f.filed_at)

    rows: list[dict[str, Any]] = []
    for (ticker, period_end), vals in sorted(quarterly.items()):
        revenue = vals.get("revenue")
        gross = vals.get("gross_profit")
        net = vals.get("net_income")
        rows.append(
            {
                "ticker": ticker,
                "period_end": period_end,
                "revenue": revenue,
                "net_income": net,
                "eps": vals.get("eps"),
                "total_debt": vals.get("total_debt"),
                "equity": vals.get("equity"),
                "gross_margin": (gross / revenue) if gross is not None and revenue else None,
                "net_margin": (net / revenue) if net is not None and revenue else None,
                "growth_yoy": None,
                "valid_from": filed_at[(ticker, period_end)].isoformat(),
            }
        )

    _derive_q4(rows, annuals)

    # Recompute margins for rows that had Q4 values filled
    for r in rows:
        if r.get("q4_derived"):
            revenue, net = r.get("revenue"), r.get("net_income")
            r["net_margin"] = (net / revenue) if net is not None and revenue else None
            r.pop("q4_derived", None)

    _fill_growth_yoy(rows)
    return rows
