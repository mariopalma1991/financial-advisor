"""Data quality checks v1 (plan §13). Cheap to run early, painful to retrofit.

- compare_closes:      cross-source price divergence (yfinance vs Stooq)
- check_freshness:     latest price bar staleness (day-granular, weekend-tolerant)
- check_screener_sanity: a screen matching nothing for N straight days is likely broken
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.adapters.base import RawPriceBar
from src.config import load_platform
from src.quality.run_metadata import EventEmitter
from src.storage.supabase_client import fetch_all


def compare_closes(
    primary_bars: list[RawPriceBar], crosscheck_bars: list[RawPriceBar], emitter: EventEmitter
) -> list[dict[str, Any]]:
    """Emit divergence_detected (WARN) when closes differ beyond the threshold."""
    threshold = float(load_platform().quality["price_divergence_pct"])
    other_map = {(b.ticker, b.bar_date): b.close for b in crosscheck_bars}
    divergences: list[dict[str, Any]] = []
    for b in primary_bars:
        other = other_map.get((b.ticker, b.bar_date))
        if other is None or not b.close or not other:
            continue
        diff = abs(b.close - other) / other
        if diff > threshold:
            div = {
                "ticker": b.ticker,
                "bar_date": b.bar_date.isoformat(),
                "primary": b.close,
                "crosscheck": other,
                "diff_pct": round(diff, 5),
            }
            divergences.append(div)
            emitter.emit(
                "WARN",
                "divergence_detected",
                ticker=b.ticker,
                message=f"close divergence {diff:.2%} on {b.bar_date}",
                payload=div,
            )
    return divergences


def check_freshness(emitter: EventEmitter) -> None:
    """WARN if the newest price bar is implausibly old (weekend/holiday tolerant)."""
    rows = fetch_all("select max(bar_date) as latest from current_price_bars")
    latest = rows[0]["latest"] if rows else None
    if latest is None:
        emitter.emit("ERROR", "freshness_check", "no price bars in the database at all")
        return
    age_days = (datetime.now(timezone.utc).date() - latest).days
    if age_days > 5:
        emitter.emit(
            "WARN",
            "freshness_check",
            f"latest price bar is {latest} ({age_days} days old)",
            payload={"latest": str(latest), "age_days": age_days},
        )


def check_screener_sanity(emitter: EventEmitter) -> None:
    """WARN if any screen has matched nothing for N consecutive result days."""
    n = int(load_platform().quality["screener_zero_days_alert"])
    rows = fetch_all(
        """
        select screen_name, result_date, count(*) filter (where matched) as matches
        from screener_results
        group by screen_name, result_date
        order by result_date desc
        """
    )
    if not rows:
        return
    by_screen: dict[str, dict[Any, int]] = defaultdict(dict)
    for r in rows:
        by_screen[r["screen_name"]][r["result_date"]] = int(r["matches"])
    recent_dates = sorted({r["result_date"] for r in rows}, reverse=True)[:n]
    for screen, per_date in by_screen.items():
        streak = 0
        for d in recent_dates:
            if per_date.get(d, None) == 0:
                streak += 1
            else:
                break
        if streak >= n:
            emitter.emit(
                "WARN",
                "screener_sanity",
                f"screen '{screen}' matched nothing for {streak} consecutive days",
                payload={"screen": screen, "zero_streak_days": streak},
            )
