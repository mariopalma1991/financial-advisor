"""Insider sentiment summary: monthly MSPR + net share change → feed windows."""
from __future__ import annotations

from typing import Any


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def summarize(rows: list[dict[str, Any]], months: int = 12) -> dict[str, Any]:
    """Summarize point-in-time monthly rows (ascending by period).

    Returns the last `months` months plus 3- and 6-month windows: mean MSPR,
    net share change, and how many of those months insiders were net buyers.
    """
    if not rows:
        return {}
    recent = rows[-months:]

    def window(n: int) -> dict[str, Any]:
        w = recent[-n:]
        msprs = [float(r["mspr"]) for r in w if r.get("mspr") is not None]
        changes = [float(r["change"]) for r in w if r.get("change") is not None]
        return {
            "mspr_avg": _avg(msprs),
            "net_change": round(sum(changes)) if changes else None,
            "months_net_buying": sum(1 for c in changes if c > 0),
            "months": len(w),
        }

    last = recent[-1]
    return {
        "latest_month": str(last["period"])[:7],
        "latest_mspr": None if last.get("mspr") is None else round(float(last["mspr"]), 2),
        "windows": {"3m": window(3), "6m": window(6)},
        "monthly": [
            {
                "month": str(r["period"])[:7],
                "mspr": None if r.get("mspr") is None else round(float(r["mspr"]), 2),
                "change": None if r.get("change") is None else round(float(r["change"])),
            }
            for r in recent
        ],
    }
