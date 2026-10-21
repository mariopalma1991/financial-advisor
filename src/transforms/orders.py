"""The investor's orders for the next trading day (pure; unit-tested). Used by
src/jobs/order_alerts.py.

The book they follow is rebuilt from what is stored (its fills and last NAV row),
then the next day is played with the same rules as the simulation (src/transforms/
paper.py): the day's deposit becomes cash, a pending signal's decisions rebalance
the book, otherwise the deposit tops up the positions below target. Prices are the
last close, so the share counts are an estimate of what the close will fill.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date

from src.transforms import paper

_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

def book_from_fills(fills: Sequence[dict], cash: float, contributed: float) -> paper.Book:
    """The book's positions (shares per ticker, from its fills in order), cash and deposits."""
    book = paper.Book(cash=cash, contributed=contributed)
    for f in fills:
        p = book.positions.setdefault(f["ticker"], paper.Position(0.0, 0.0))
        if f["side"] == "buy":
            p.shares += f["shares"]
            p.cost_mxn += (f["amount_mxn"] or 0.0) + (f["cost_mxn"] or 0.0)
        else:
            book.positions.pop(f["ticker"], None)
    return book


def preview(book: paper.Book, pending: Mapping[str, Sequence[dict]], flow: float, day: str,
            px: Callable[[str, str], float | None], fx: float, rules: paper.Rules) -> list[dict]:
    """The fills of `day`, as simulate would make them: `pending` signal day ->
    decisions not filled yet, `flow` the deposits that become cash that day."""
    book.cash += flow
    book.contributed += flow
    start = len(book.fills)
    one = lambda t, d: 1.0   # no split after a day that hasn't happened
    for sd in sorted(pending):
        paper.rebalance(book, pending[sd], day, px, fx, rules, sd, one)
    if flow and not pending:
        paper.top_up(book, day, px, fx, rules, day, one)
    return book.fills[start:]


def signature(fills: Sequence[dict], vetoed: Sequence[str]) -> str:
    """What the investor has to do, so an unchanged list isn't sent twice."""
    key = sorted((f["side"], f["ticker"], round(f["shares"], 6)) for f in fills), sorted(vetoed)
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:16]


def day_name(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{_DAYS[x.weekday()]} {x.day} {_MONTHS[x.month - 1]}"


def _price(t: str, usd: Mapping[str, float], fx: float) -> str:
    return f"({usd[t]:,.2f} USD / {usd[t] * fx:,.0f} MXN)"


def message(*, fills: Sequence[dict], holdings: Mapping[str, tuple[float, float]], usd: Mapping[str, float],
            fx: float) -> str:
    """The day's action in one line: VENTA x (precio) - COMPRA y (precio), or HOLD x (precio).
    holdings: ticker -> (shares, MXN) held after today's trades; usd: ticker -> last close (USD);
    fx: USD/MXN at that close."""
    sells = [f["ticker"] for f in fills if f["side"] == "sell"]
    buys = list(dict.fromkeys(f["ticker"] for f in fills if f["side"] == "buy"))
    parts = [f"VENTA {t} {_price(t, usd, fx)}" for t in sells] + [f"COMPRA {t} {_price(t, usd, fx)}" for t in buys]
    if parts:
        return " - ".join(parts)
    if holdings:
        return " - ".join(f"HOLD {t} {_price(t, usd, fx)}" for t in sorted(holdings))
    return "HOLD (todo en efectivo)"
