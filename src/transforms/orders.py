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

SELL_REASONS = {
    "rank_below_keep": "salió del lugar {keep}",
    "ineligible": "ya no es elegible",
    "not_scored": "sin calificación de factores",
    "too_many_positions_or_industry": "sobra por tope de posiciones o de industria",
    "not_in_capa2": "Capa 2 ya no la tiene",
}


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


def _mxn(x: float) -> str:
    return f"{x:,.0f} MXN"


def _committee(reason: str | None) -> str:
    """The committee's verdict on a buy of a book with the veto ('' for a book without it)."""
    if reason == "no_review":
        return "sin revisión del comité"
    if reason and reason.startswith("approved_"):
        n, _, of = reason.removeprefix("approved_").partition("_of_")
        return f"comité aprueba {n} de {of}"
    return ""


def _vetoes(reason: str | None) -> str:
    n, _, of = (reason or "").removeprefix("vetoed_").partition("_of_")
    return f"{n} de {of} modelos" if of else "vetada"


def headline(fills: Sequence[dict], holdings: Mapping[str, tuple[float, float]]) -> str:
    """VENTA x · COMPRA y, or HOLD with what is held."""
    sells = [f["ticker"] for f in fills if f["side"] == "sell"]
    buys = list(dict.fromkeys(f["ticker"] for f in fills if f["side"] == "buy"))
    parts = ([f"VENTA {', '.join(sells)}"] if sells else []) + ([f"COMPRA {', '.join(buys)}"] if buys else [])
    return " · ".join(parts) or ("HOLD " + ", ".join(sorted(holdings)) if holdings else "HOLD (todo en efectivo)")


def message(*, exec_day: str, close_cst: str, prices_day: str, book_label: str, fills: Sequence[dict],
            decisions: Mapping[str, dict], vetoed: Sequence[dict], flow: float, cash_after: float,
            holdings: Mapping[str, tuple[float, float]], capa2_extra: Sequence[str], keep_rank: int,
            simulated: bool, bmv_closed: bool = False) -> str:
    """The text sent to the investor. decisions: ticker -> the book's pending decision row;
    holdings: ticker -> (shares, MXN at the last close) after today's trades."""
    lines = [f"{headline(fills, holdings)} — {day_name(exec_day)}",
             book_label + (" · simulación, sin dinero real" if simulated else ""), ""]
    if bmv_closed:
        lines += ["Ojo: hoy la BMV cierra y el SIC no opera; captura las órdenes el siguiente día hábil.", ""]
    if flow:
        lines += [f"Depósito de hoy: {_mxn(flow)}. Deposítalo en GBM antes de comprar.", ""]
    sells = [f for f in fills if f["side"] == "sell"]
    buys = [f for f in fills if f["side"] == "buy"]
    if sells:
        lines.append(f"VENDER al cierre ({close_cst} CST), todas las acciones de la posición")
        for f in sells:
            d = decisions.get(f["ticker"]) or {}
            why = SELL_REASONS.get(d.get("reason") or f.get("reason"), d.get("reason") or "")
            why = why.format(keep=keep_rank) if keep_rank > 1 else why.replace("salió del lugar 1", "ya no es el #1")
            if d.get("rank") and d.get("reason") == "rank_below_keep":
                why += f" (va en el {d['rank']})"
            lines.append(f"• {f['shares']:g} {f['ticker']} ≈ {_mxn(f['amount_mxn'])} · {why}")
        lines.append("")
    if buys:
        lines.append(f"COMPRAR al cierre ({close_cst} CST)")
        for f in buys:
            d = decisions.get(f["ticker"])
            if d and d.get("action") == "buy":
                what = ", ".join(x for x in ("nueva", d.get("rank") and f"lugar {d['rank']}",
                                             _committee(d.get("reason"))) if x)
            else:
                what = "más de una posición que ya tienes"
            lines.append(f"• {f['shares']:g} {f['ticker']} ≈ {_mxn(f['amount_mxn'])} ({f['price_usd']:,.2f} USD c/u) · {what}")
        lines.append("")
    if not fills:
        for t, (shares, value) in sorted(holdings.items()):
            rank = (decisions.get(t) or {}).get("rank")
            lines.append(f"Mantén tus {shares:g} {t} (≈ {_mxn(value)})" + (f"; sigue en el lugar {rank}." if rank else "."))
        if flow:
            lines.append("El depósito no alcanza para una acción entera; queda en efectivo.")
        lines.append("")
    if vetoed:
        lines.append("Vetadas por el comité (no comprar): " +
                     ", ".join(f"{v['ticker']} ({_vetoes(v.get('reason'))})" for v in vetoed))
    if capa2_extra:
        lines.append("Capa 2 sin veto compraría además: " + ", ".join(capa2_extra))
    if fills:
        n = len(holdings)
        lines.append(f"Después: {n} {'posición' if n == 1 else 'posiciones'} y ~{_mxn(cash_after)} en efectivo.")
        lines.append(f"Cantidades con el cierre del {day_name(prices_day)}; si el precio se mueve, puede cambiar "
                     "1 acción. Orden a mercado en el SIC antes del cierre.")
    else:
        lines.append(f"Datos del cierre del {day_name(prices_day)}.")
    return "\n".join(lines).strip()
