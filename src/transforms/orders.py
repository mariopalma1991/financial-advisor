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
    if not reason or reason == "no_review":
        return "sin revisión del comité"
    if reason.startswith("approved_"):
        n, _, of = reason.removeprefix("approved_").partition("_of_")
        return f"comité aprueba {n} de {of}"
    return reason


def _vetoes(reason: str | None) -> str:
    n, _, of = (reason or "").removeprefix("vetoed_").partition("_of_")
    return f"{n} de {of} modelos" if of else "vetada"


def message(*, exec_day: str, close_cst: str, prices_day: str, book_label: str, fills: Sequence[dict],
            decisions: Mapping[str, dict], vetoed: Sequence[dict], flow: float, cash_after: float,
            positions_after: int, capa2_extra: Sequence[str], keep_rank: int, simulated: bool,
            correction: bool = False, bmv_closed: bool = False) -> str:
    """The text sent to the investor. decisions: ticker -> the book's pending decision row."""
    head = f"Órdenes para el {day_name(exec_day)}, al cierre ({close_cst} CST)"
    lines = []
    if correction:
        lines.append("CORRECCIÓN: cambió la lista que te mandé para este día. Esta es la buena.")
    lines += [head + (" · simulación, sin dinero real" if simulated else ""), f"Libro: {book_label}", ""]
    if bmv_closed:
        lines += ["Ojo: ese día la BMV cierra y el SIC no opera; captura las órdenes el siguiente día hábil.", ""]
    if flow:
        lines += [f"Depósito: {_mxn(flow)}. Deposítalo en GBM antes de comprar.", ""]
    sells = [f for f in fills if f["side"] == "sell"]
    buys = [f for f in fills if f["side"] == "buy"]
    if sells:
        lines.append("VENDER (todas las acciones de la posición)")
        for f in sells:
            d = decisions.get(f["ticker"]) or {}
            why = SELL_REASONS.get(d.get("reason") or f.get("reason"), d.get("reason") or "")
            why = why.format(keep=keep_rank)
            if d.get("rank") and d.get("reason") == "rank_below_keep":
                why += f" (va en el {d['rank']})"
            lines.append(f"• {f['shares']:g} {f['ticker']} ≈ {_mxn(f['amount_mxn'])} · {why}")
        lines.append("")
    if buys:
        lines.append("COMPRAR")
        for f in buys:
            d = decisions.get(f["ticker"])
            unit = f["price_usd"]
            if d and d.get("action") == "buy":
                what = f"nueva, lugar {d['rank']}, {_committee(d.get('reason'))}" if d.get("rank") else \
                    f"nueva, {_committee(d.get('reason'))}"
            else:
                what = "recarga de una posición que ya tienes"
            lines.append(f"• {f['shares']:g} {f['ticker']} ≈ {_mxn(f['amount_mxn'])} ({unit:,.2f} USD c/u) · {what}")
        lines.append("")
    if not fills:
        lines += ["Sin órdenes: el dinero no alcanza para una acción entera; queda en efectivo.", ""]
    if vetoed:
        lines.append("Vetadas por el comité (no comprar): " +
                     ", ".join(f"{v['ticker']} ({_vetoes(v.get('reason'))})" for v in vetoed))
    if capa2_extra:
        lines.append("Capa 2 sin veto compraría además: " + ", ".join(capa2_extra))
    lines += [f"Después: {positions_after} posiciones y ~{_mxn(cash_after)} en efectivo.",
              (f"Cantidades con el cierre del {day_name(prices_day)}; si el precio se mueve, puede cambiar 1 acción. "
               "Orden a mercado en el SIC antes del cierre.")]
    return "\n".join(lines).strip()
