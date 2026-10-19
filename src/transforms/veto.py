"""Capa 4: Capa 2's decisions with the committee's veto (pure; unit-tested). Used by
src/jobs/committee_portfolio.py, which runs them through src/transforms/paper.simulate.

On each signal day the book does what Capa 2 decided for the names it holds, and buys
Capa 2's new names unless the committee vetoed them:
  - Capa 2 buys a name: the book buys it, unless at least `veto_votes` of the book's
    models vetoed it (action 'veto'). With no review in time it buys (reason
    'no_review'): a veto needs a review.
  - Capa 2 holds a name the book does not have (vetoed before): nothing, it is not
    bought later; the slot's money waits for Capa 2's next buy.
  - The book holds a name Capa 2 sells, or no longer lists: sell.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def verdict(reviews: Sequence[dict], veto_votes: int) -> tuple[bool, str]:
    """(vetoed, reason) from one model's review per entry ({model, vetoed})."""
    if not reviews:
        return False, "no_review"
    vetoes = sum(1 for r in reviews if r["vetoed"])
    if vetoes >= veto_votes:
        return True, f"vetoed_{vetoes}_of_{len(reviews)}"
    return False, f"approved_{len(reviews) - vetoes}_of_{len(reviews)}"


def apply_veto(capa2: Sequence[dict], held: Sequence[str],
               reviews: Mapping[str, Sequence[dict]], veto_votes: int) -> list[dict]:
    """The book's decisions for one signal day. capa2: Capa 2's decision rows
    (ticker, action, reason, rank, score, industry); reviews: ticker -> the book's
    models' reviews of that buy."""
    out: list[dict] = []
    listed = set()
    for d in capa2:
        t = d["ticker"]
        listed.add(t)
        row = {k: d.get(k) for k in ("ticker", "rank", "score", "industry")}
        if d["action"] == "buy" and t not in held:
            vetoed, why = verdict(reviews.get(t, ()), veto_votes)
            out.append({**row, "action": "veto" if vetoed else "buy", "reason": why})
        elif d["action"] in ("buy", "hold") and t in held:
            out.append({**row, "action": "hold", "reason": d.get("reason")})
        elif d["action"] == "sell" and t in held:
            out.append({**row, "action": "sell", "reason": d.get("reason")})
    for t in held:
        if t not in listed:
            out.append({"ticker": t, "action": "sell", "reason": "not_in_capa2", "rank": None,
                        "score": None, "industry": None})
    return out
