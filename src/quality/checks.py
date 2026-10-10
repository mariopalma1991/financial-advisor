"""Data quality checks v1 (plan §13). Cheap to run early, painful to retrofit.

- check_freshness:     latest price bar staleness (day-granular, weekend-tolerant)
- check_screener_sanity: a screen matching nothing for N straight days is likely broken
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.config import load_platform
from src.quality.run_metadata import EventEmitter
from src.storage.supabase_client import _exec, _paginate, get_client


def check_freshness(emitter: EventEmitter) -> None:
    """WARN if the newest price bar is implausibly old (weekend/holiday tolerant)."""
    rows = _exec(
        get_client()
        .table("current_price_bars")
        .select("bar_date")
        .order("bar_date", desc=True)
        .limit(1)
    )
    latest_str = rows[0]["bar_date"] if rows else None
    if latest_str is None:
        emitter.emit("ERROR", "freshness_check", "no price bars in the database at all")
        return
    latest = datetime.fromisoformat(str(latest_str)).date()
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
    rows = _paginate(
        get_client()
        .table("screener_results")
        .select("*")
        .order("result_date", desc=True)
    )
    if not rows:
        return

    # Group by (screen_name, result_date) and count matches
    by_screen: dict[str, dict[Any, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r.get("matched"):
            by_screen[r["screen_name"]][r["result_date"]] += 1
        else:
            by_screen[r["screen_name"]].setdefault(r["result_date"], 0)

    recent_dates = sorted({r["result_date"] for r in rows}, reverse=True)[:n]
    for screen, per_date in by_screen.items():
        streak = 0
        for d in recent_dates:
            if per_date.get(d, 0) == 0:
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
