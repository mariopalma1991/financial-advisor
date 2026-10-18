"""Cross-sectional factor scores for the stock screen (pure; unit-tested).

Five factors with long published evidence, each a 0-centred z-score compared
within the company's industry:
  momentum   12-month return skipping the last month (split-adjusted closes)
  revisions  how analysts moved EPS estimates for this and next fiscal year
             over 30/90 days, and the share of revisions that went up
  pead       post-earnings drift: the last EPS surprise (if recent) and the
             stock's reaction around the report
  insider    open-market buys by insiders in 90 days (sales and 10b5-1 plans ignored)
  quality    ROE, gross margin, low debt and stable operating margin
The score is the mean of the five; a factor with no data counts as 0 (neutral).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import pandas as pd

FACTORS = ("momentum", "revisions", "pead", "insider", "quality")
MIN_GROUP = 10        # smaller industries are pooled before computing z-scores
Z_CAP = 3.0
WINSOR = (0.025, 0.975)
SKIP_DAYS = 21        # momentum skips the last month (short-term reversal)
LOOKBACK_DAYS = 252


def adjust_for_splits(bars: Sequence[dict], splits: Iterable[dict]) -> list[float]:
    """Closes (ascending bars) divided by every later split's ratio, so a return
    across a split is not mistaken for a crash or a jump."""
    closes = [b["close"] for b in bars]
    for s in splits:
        ratio = s.get("ratio")
        if not ratio:
            continue
        for i, b in enumerate(bars):
            if b["bar_date"] < s["ex_date"]:
                closes[i] = closes[i] / ratio
    return closes


def momentum_12_1(closes: Sequence[float]) -> float | None:
    """Return from ~12 months ago to ~1 month ago."""
    if len(closes) <= LOOKBACK_DAYS:
        return None
    start, end = closes[-1 - LOOKBACK_DAYS], closes[-1 - SKIP_DAYS]
    return end / start - 1 if start and start > 0 and end else None


def avg_dollar_volume(bars: Sequence[dict], n: int = 20) -> float | None:
    vals = [b["close"] * b["volume"] for b in bars[-n:] if b.get("close") and b.get("volume")]
    return sum(vals) / len(vals) if vals else None


def window_return(bars: Sequence[dict], closes: Sequence[float], day: str, before: int = 1,
                  after: int = 1) -> float | None:
    """Return from `before` bars before `day` to `after` bars after it (the
    reaction to a report: works for before-open and after-close releases)."""
    dates = [b["bar_date"] for b in bars]
    idx = next((i for i, d in enumerate(dates) if d >= day), None)
    if idx is None or idx - before < 0 or idx + after >= len(closes):
        return None
    a, b = closes[idx - before], closes[idx + after]
    return b / a - 1 if a and a > 0 and b else None


def _rel(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def revision_signals(trends: Iterable[dict]) -> tuple[float | None, float | None]:
    """(estimate change, revision breadth) from the current-year and next-year rows
    of one estimate_trends snapshot. Change = mean of the 30- and 90-day relative
    moves in the consensus EPS; breadth = (ups - downs) / analysts over 30 days."""
    chg, breadth = [], []
    for r in trends:
        if r.get("period") not in ("0y", "+1y"):
            continue
        for old in ("eps_30d_ago", "eps_90d_ago"):
            v = _rel(r.get("eps_current"), r.get(old))
            if v is not None:
                chg.append(max(-1.0, min(1.0, v)))
        n = r.get("n_analysts")
        if n:
            breadth.append(((r.get("up_30d") or 0) - (r.get("down_30d") or 0)) / n)
    return _mean(chg), _mean(breadth)


def insider_signal(n_buyers: int, buy_value: float) -> float:
    """More distinct buyers is the stronger signal (clusters); a large buy adds a bit."""
    return min(n_buyers, 4) + (0.5 if buy_value >= 100_000 else 0.0)


def quality_inputs(quarters: Sequence[dict]) -> dict[str, float | None]:
    """Latest ROE, gross margin, debt/equity and the volatility of operating margin
    over up to 8 quarters (newest first)."""
    if not quarters:
        return {"roe_ttm": None, "gross_margin": None, "debt_to_equity": None, "op_margin_std": None}
    last = quarters[0]
    om = [q["operating_margin"] for q in quarters[:8] if q.get("operating_margin") is not None]
    std = None
    if len(om) >= 4:
        m = sum(om) / len(om)
        std = math.sqrt(sum((x - m) ** 2 for x in om) / (len(om) - 1))
    return {"roe_ttm": last.get("roe_ttm"), "gross_margin": last.get("gross_margin"),
            "debt_to_equity": last.get("debt_to_equity"), "op_margin_std": std}


def winsorize(s: pd.Series) -> pd.Series:
    if s.notna().sum() < 5:
        return s
    lo, hi = s.quantile(WINSOR[0]), s.quantile(WINSOR[1])
    return s.clip(lo, hi)


def group_z(s: pd.Series, groups: pd.Series) -> pd.Series:
    """z-score of each value within its group (industries under MIN_GROUP members
    with data are pooled), capped at +/-Z_CAP. Missing stays missing."""
    s = winsorize(s.astype(float))
    has = s.notna()
    counts = groups[has].value_counts()
    g = groups.where(groups.map(counts).fillna(0) >= MIN_GROUP, "_pooled")
    out = pd.Series(float("nan"), index=s.index)
    for idx in g[has].groupby(g[has]).groups.values():
        vals = s.loc[idx]
        sd = vals.std(ddof=0)
        if len(vals) < 3 or not sd or math.isnan(sd):
            out.loc[idx] = 0.0
        else:
            out.loc[idx] = ((vals - vals.mean()) / sd).clip(-Z_CAP, Z_CAP)
    return out


def _mean_z(parts: list[pd.Series]) -> pd.Series:
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


_INPUTS = ("mom_12_1", "rev_chg", "rev_breadth", "sue", "ear", "insider_raw",
           "roe_ttm", "gross_margin", "debt_to_equity", "op_margin_std")


def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Add z_<factor>, score, n_factors and rank to a frame of raw inputs, one row
    per ticker, scoring only rows with eligible=True (the others keep raw values)."""
    out = df.copy()
    for f in FACTORS:
        out[f"z_{f}"] = float("nan")
    out["score"] = float("nan")
    out["n_factors"] = 0
    out["rank"] = pd.NA
    el = out[out["eligible"]].copy()
    if el.empty:
        return out
    for c in _INPUTS:
        el[c] = pd.to_numeric(el[c], errors="coerce")
    g = el["industry"].fillna("_none")
    z = {
        "momentum": group_z(el["mom_12_1"], g),
        "revisions": _mean_z([group_z(el["rev_chg"], g), group_z(el["rev_breadth"], g)]),
        "pead": _mean_z([group_z(el["sue"], g), group_z(el["ear"], g)]),
        "insider": group_z(el["insider_raw"], g),
        "quality": _mean_z([group_z(el["roe_ttm"], g), group_z(el["gross_margin"], g),
                            group_z(-el["debt_to_equity"], g), group_z(-el["op_margin_std"], g)]),
    }
    zf = pd.DataFrame(z)
    out.loc[el.index, [f"z_{f}" for f in FACTORS]] = zf.to_numpy()
    out.loc[el.index, "n_factors"] = zf.notna().sum(axis=1).astype(int)
    out.loc[el.index, "score"] = zf.fillna(0.0).mean(axis=1)
    order = out.loc[el.index, "score"].rank(ascending=False, method="first")
    out.loc[el.index, "rank"] = order.astype(int)
    return out
