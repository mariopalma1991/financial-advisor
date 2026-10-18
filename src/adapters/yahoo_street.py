"""Yahoo Finance "street" data: EPS estimate trends and revisions, analyst price
targets, short interest and analyst rating changes.

One quoteSummary request per ticker (four modules) instead of the four separate
requests yfinance's public properties would make. Yahoo has no official API and
throttles bursts, so the job spaces calls and runs weekly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

_MODULES = ["earningsTrend", "financialData", "defaultKeyStatistics", "upgradeDowngradeHistory"]
_PERIODS = ("0q", "+1q", "0y", "+1y")
RATING_LOOKBACK_DAYS = 365


def _raw(v: Any) -> Any:
    """quoteSummary values come as {"raw": x, "fmt": ...} or as plain numbers."""
    if isinstance(v, dict):
        return v.get("raw")
    return v


def _num(v: Any) -> float | None:
    v = _raw(v)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _epoch_date(v: Any) -> str | None:
    n = _num(v)
    return datetime.fromtimestamp(n, tz=timezone.utc).date().isoformat() if n else None


def _ci(d: dict, *names: str) -> Any:
    """Case-insensitive key lookup: Yahoo spells e.g. downLast7Days / downLast30days."""
    low = {k.lower(): v for k, v in (d or {}).items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def parse_trends(ticker: str, summary: dict, snapshot: date) -> list[dict]:
    rows = []
    for item in (summary.get("earningsTrend") or {}).get("trend") or []:
        period = item.get("period")
        if period not in _PERIODS:
            continue
        est = item.get("earningsEstimate") or {}
        rev = item.get("revenueEstimate") or {}
        tr = item.get("epsTrend") or {}
        rv = item.get("epsRevisions") or {}
        row = {
            "ticker": ticker, "snapshot_date": snapshot.isoformat(), "period": period,
            "end_date": item.get("endDate"),
            "eps_avg": _num(est.get("avg")), "eps_low": _num(est.get("low")),
            "eps_high": _num(est.get("high")), "n_analysts": _int(est.get("numberOfAnalysts")),
            "eps_growth": _num(est.get("growth")), "revenue_avg": _num(rev.get("avg")),
            "eps_current": _num(tr.get("current")), "eps_7d_ago": _num(_ci(tr, "7daysAgo")),
            "eps_30d_ago": _num(_ci(tr, "30daysAgo")), "eps_60d_ago": _num(_ci(tr, "60daysAgo")),
            "eps_90d_ago": _num(_ci(tr, "90daysAgo")),
            "up_7d": _int(_ci(rv, "upLast7days")), "up_30d": _int(_ci(rv, "upLast30days")),
            "down_7d": _int(_ci(rv, "downLast7days")), "down_30d": _int(_ci(rv, "downLast30days")),
        }
        if any(row[k] is not None for k in ("eps_avg", "eps_current", "revenue_avg")):
            rows.append(row)
    return rows


def parse_street(ticker: str, summary: dict, snapshot: date) -> dict | None:
    fd = summary.get("financialData") or {}
    ks = summary.get("defaultKeyStatistics") or {}
    row = {
        "ticker": ticker, "snapshot_date": snapshot.isoformat(),
        "target_low": _num(fd.get("targetLowPrice")), "target_mean": _num(fd.get("targetMeanPrice")),
        "target_median": _num(fd.get("targetMedianPrice")), "target_high": _num(fd.get("targetHighPrice")),
        "n_target_analysts": _int(fd.get("numberOfAnalystOpinions")),
        "recommendation_mean": _num(fd.get("recommendationMean")),
        "shares_short": _int(ks.get("sharesShort")),
        "shares_short_prior_month": _int(ks.get("sharesShortPriorMonth")),
        "short_interest_date": _epoch_date(ks.get("dateShortInterest")),
        "short_pct_float": _num(ks.get("shortPercentOfFloat")),
        "short_ratio": _num(ks.get("shortRatio")),
        "float_shares": _int(ks.get("floatShares")),
    }
    return row if any(v is not None for k, v in row.items() if k not in ("ticker", "snapshot_date")) else None


def parse_ratings(ticker: str, summary: dict, snapshot: date) -> list[dict]:
    since = snapshot - timedelta(days=RATING_LOOKBACK_DAYS)
    rows: dict[tuple, dict] = {}
    for h in (summary.get("upgradeDowngradeHistory") or {}).get("history") or []:
        d = _epoch_date(h.get("epochGradeDate"))
        firm = (h.get("firm") or "").strip()
        if not d or not firm or date.fromisoformat(d) < since:
            continue
        rows[(d, firm)] = {
            "ticker": ticker, "grade_date": d, "firm": firm,
            "to_grade": h.get("toGrade") or None, "from_grade": h.get("fromGrade") or None,
            "action": h.get("action") or None,
            "price_target_action": h.get("priceTargetAction") or None,
            "current_price_target": _num(h.get("currentPriceTarget")),
            "prior_price_target": _num(h.get("priorPriceTarget")),
        }
    return list(rows.values())


def parse_summary(ticker: str, payload: dict | None, snapshot: date) -> list[dict]:
    """Tagged rows ({"kind": "trend"|"street"|"rating", ...}) for one ticker."""
    try:
        summary = (payload or {})["quoteSummary"]["result"][0] or {}
    except (KeyError, IndexError, TypeError):
        return []
    out = [{"kind": "trend", **r} for r in parse_trends(ticker, summary, snapshot)]
    street = parse_street(ticker, summary, snapshot)
    if street:
        out.append({"kind": "street", **street})
    out += [{"kind": "rating", **r} for r in parse_ratings(ticker, summary, snapshot)]
    return out


class YahooRateLimited(RuntimeError):
    """Yahoo answered 429/999; status_code lets the symbol loop back off and retry."""
    status_code = 429


class YahooStreetAdapter:
    name = "yahoo"

    def __init__(self, snapshot: date | None = None):
        self.snapshot = snapshot or datetime.now(timezone.utc).date()

    def fetch(self, ticker: str) -> list[dict]:
        # Quote._fetch is yfinance's own quoteSummary call (pinned yfinance==1.7.0);
        # it lets one request carry all four modules.
        try:
            payload = yf.Ticker(ticker)._quote._fetch(modules=list(_MODULES))
        except YFRateLimitError as exc:
            raise YahooRateLimited(f"yahoo rate limit on {ticker}") from exc
        # yfinance logs HTTP errors and returns None; raise so the loop counts them
        # (a blocked run must turn red, not "complete" with zero rows).
        if payload is None:
            raise RuntimeError(f"yahoo quoteSummary: no response for {ticker}")
        err = (payload.get("quoteSummary") or {}).get("error")
        if err:
            raise RuntimeError(f"yahoo quoteSummary error for {ticker}: {err}")
        return parse_summary(ticker, payload, self.snapshot)
