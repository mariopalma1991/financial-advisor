"""Split/dividend adjustment computed as of a date (plan §6 critical rule).

Bars are stored as Yahoo serves them: the close is split-adjusted as of the
day it was fetched (not dividend-adjusted). So a split is applied only to bars
fetched before its ex_date, and dividends to every earlier bar; actions with
ex_date <= as_of only. Because the action set is filtered by
as_of, historical simulations never see dividends/splits before they happened.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

_PRICE_COLS = ("open", "high", "low", "close")


def adjust_bars(
    bars: pd.DataFrame, actions: pd.DataFrame, as_of: date | None = None
) -> pd.DataFrame:
    """Return price-adjusted bars.

    bars:    indexed by bar_date (ascending), raw open/high/low/close columns.
    actions: columns ex_date (date), action_type ('dividend'|'split'), amount, ratio.
    as_of:   only actions with ex_date <= as_of are applied.
    """
    if bars.empty:
        return bars
    out = bars.copy()
    if actions is None or actions.empty:
        return out

    actions = actions.sort_values("ex_date")
    if as_of is not None:
        actions = actions[actions["ex_date"] <= as_of]

    factor = pd.Series(1.0, index=out.index)
    for _, a in actions.iterrows():
        prior_mask = out.index < a["ex_date"]
        if not prior_mask.any():
            continue
        if a["action_type"] == "split" and a.get("ratio"):
            # Yahoo's close is already split-adjusted as of the day it was fetched, so
            # only bars stored before the split need it (see _stored_before)
            split_mask = prior_mask & _stored_before(out, a["ex_date"])
            factor[split_mask] /= float(a["ratio"])
        elif a["action_type"] == "dividend" and a.get("amount"):
            ref = float(out.loc[prior_mask, "close"].iloc[-1])
            if ref > 0:
                factor[prior_mask] *= 1 - float(a["amount"]) / ref

    for col in _PRICE_COLS:
        if col in out.columns:
            out[col] = out[col] * factor
    return out


def _stored_before(bars: pd.DataFrame, ex_date: date) -> pd.Series:
    """True for bars fetched before ex_date (raw prices then). Bars without an
    ingested_at column are treated as raw, the old behaviour."""
    if "ingested_at" not in bars.columns:
        return pd.Series(True, index=bars.index)
    fetched = pd.to_datetime(bars["ingested_at"], utc=True, errors="coerce").dt.date
    return fetched.isna() | (fetched < ex_date)
