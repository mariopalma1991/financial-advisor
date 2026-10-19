"""How a committee alert did afterwards, in MXN against VOO (pure; unit-tested).
Used by src/jobs/score_alerts.py."""
from __future__ import annotations

import bisect
from collections.abc import Sequence

HORIZONS = (5, 20, 40, 60)   # trading days: 1, 4, 8 and 12 weeks (Mario, 2026-10-19: drop weak models fast)


class Series:
    """Last known value on or before a day, for (day, value) pairs."""

    def __init__(self, pairs: Sequence[tuple[str, float]]):
        pairs = sorted(pairs)
        self.days = [d for d, _ in pairs]
        self.vals = [v for _, v in pairs]

    def at(self, day: str) -> float | None:
        i = bisect.bisect_right(self.days, day)
        return self.vals[i - 1] if i else None


def outcome(days: Sequence[str], alert_day: str, horizon: int, stock: Series, bench: Series,
            usd_mxn: Series) -> dict | None:
    """Stock and benchmark return in MXN from the close of the first trading day
    after the alert (the alert used that day's data, so its close is not
    tradable) to `horizon` trading days later. None until that day has a close."""
    i = bisect.bisect_right(days, alert_day)
    if i + horizon >= len(days):
        return None
    a, b = days[i], days[i + horizon]

    def ret(s: Series) -> float | None:
        p0, p1, f0, f1 = s.at(a), s.at(b), usd_mxn.at(a), usd_mxn.at(b)
        return p1 * f1 / (p0 * f0) - 1 if p0 and p1 and f0 and f1 else None

    r, rb = ret(stock), ret(bench)
    return {"entry_day": a, "exit_day": b, "ret_mxn": r, "bench_ret_mxn": rb,
            "excess": r - rb if r is not None and rb is not None else None}
