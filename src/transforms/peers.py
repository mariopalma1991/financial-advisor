"""Peer groups from SEC SIC codes (src/jobs/ingest_sic.py).

Each company is compared with up to MAX_PEERS companies of the same 4-digit SIC
code, or of the same 3-digit group when fewer than MIN_PEERS share the code, the
closest in market cap first. With fewer than MIN_PEERS even at 3 digits there is
no SIC group and the caller keeps Finnhub's peers.
"""
from __future__ import annotations

from math import inf, log

MAX_PEERS = 12
MIN_PEERS = 4


def _gap(a: float | None, b: float | None) -> float:
    return abs(log(a) - log(b)) if a and b and a > 0 and b > 0 else inf


def pick_peers(universe: dict[str, tuple[int | None, float | None]]) -> dict[str, tuple[list[str], str]]:
    """{ticker: (sic, market_cap)} -> {ticker: (peers, 'sic4' | 'sic3')} for the
    tickers with enough same-industry companies."""
    by4: dict[int, list[str]] = {}
    by3: dict[int, list[str]] = {}
    for t, (sic, _) in universe.items():
        if sic:
            by4.setdefault(sic, []).append(t)
            by3.setdefault(sic // 10, []).append(t)
    out = {}
    for t, (sic, cap) in universe.items():
        if not sic:
            continue
        for basis, pool in (("sic4", by4[sic]), ("sic3", by3[sic // 10])):
            cand = [p for p in pool if p != t]
            if len(cand) >= MIN_PEERS:
                cand.sort(key=lambda p: (_gap(cap, universe[p][1]), p))
                out[t] = (cand[:MAX_PEERS], basis)
                break
    return out
