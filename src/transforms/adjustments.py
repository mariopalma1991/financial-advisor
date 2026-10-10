"""Split/dividend adjustment computed as of a date (plan §6 critical rule).

Raw bars are stored unadjusted; adjustment factors are applied backward from
corporate actions with ex_date <= as_of. Because the action set is filtered by
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
            factor[prior_mask] /= float(a["ratio"])
        elif a["action_type"] == "dividend" and a.get("amount"):
            ref = float(out.loc[prior_mask, "close"].iloc[-1])
            if ref > 0:
                factor[prior_mask] *= 1 - float(a["amount"]) / ref

    for col in _PRICE_COLS:
        if col in out.columns:
            out[col] = out[col] * factor
    return out
