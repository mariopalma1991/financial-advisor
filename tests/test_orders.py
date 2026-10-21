from src.jobs.order_alerts import close_utc, next_trading_day
from src.transforms import orders, paper

RULES = paper.Rules(n=20, cost_per_side=0.0, initial_mxn=0.0, isr_on_gains=0.0, whole_shares=True,
                    min_position_mxn=2000.0)
PX = {"A": 10.0, "B": 20.0, "C": 50.0}
px = lambda t, d: PX.get(t)


def _d(t, action, reason, rank=1):
    return {"ticker": t, "action": action, "reason": reason, "rank": rank}


def test_book_from_fills_keeps_open_positions():
    fills = [{"ticker": "A", "side": "buy", "shares": 3, "amount_mxn": 30, "cost_mxn": 0},
             {"ticker": "A", "side": "buy", "shares": 1, "amount_mxn": 10, "cost_mxn": 0},
             {"ticker": "B", "side": "buy", "shares": 2, "amount_mxn": 40, "cost_mxn": 0},
             {"ticker": "B", "side": "sell", "shares": 2, "amount_mxn": 40, "cost_mxn": 0}]
    book = orders.book_from_fills(fills, cash=100.0, contributed=180.0)
    assert {t: p.shares for t, p in book.positions.items()} == {"A": 4}
    assert book.cash == 100.0 and book.contributed == 180.0


def test_preview_rebalances_a_pending_signal_like_the_simulation():
    # 4,000 MXN: two slots of 2,000; sell B, buy C, A (vetoed) is not in the book's decisions
    book = paper.Book(cash=2000.0, positions={"B": paper.Position(100.0, 2000.0)}, contributed=4000.0)
    pending = {"2026-10-30": [_d("B", "sell", "rank_below_keep", 57), _d("C", "buy", "approved_3_of_3", 4)]}
    fills = orders.preview(book, pending, 0.0, "2026-11-02", px, 1.0, RULES)
    assert [(f["side"], f["ticker"], f["shares"]) for f in fills] == [("sell", "B", 100.0), ("buy", "C", 40.0)]
    assert book.cash == 2000.0


def test_preview_spends_a_deposit_on_the_position_below_target():
    book = paper.Book(cash=0.0, positions={"A": paper.Position(200.0, 2000.0), "C": paper.Position(30.0, 1500.0)},
                      contributed=3500.0)
    fills = orders.preview(book, {}, 500.0, "2026-11-16", px, 1.0, RULES)
    assert [(f["side"], f["ticker"], f["shares"]) for f in fills] == [("buy", "C", 10.0)]
    assert orders.preview(paper.Book(cash=0.0), {}, 0.0, "2026-11-17", px, 1.0, RULES) == []


def test_signature_ignores_order_and_changes_with_a_veto():
    a = {"side": "buy", "ticker": "A", "shares": 1.0}
    b = {"side": "sell", "ticker": "B", "shares": 2.0}
    assert orders.signature([a, b], []) == orders.signature([b, a], [])
    assert orders.signature([a], []) != orders.signature([a], ["B"])


def test_message_lists_sells_buys_vetoes_and_the_deposit():
    fills = [{"side": "sell", "ticker": "B", "shares": 2.0, "amount_mxn": 1500.0, "price_usd": 40.0, "reason": "x"},
             {"side": "buy", "ticker": "OSCR", "shares": 3.0, "amount_mxn": 1870.0, "price_usd": 33.66},
             {"side": "buy", "ticker": "DAR", "shares": 1.0, "amount_mxn": 1096.0, "price_usd": 59.22}]
    decisions = {"B": _d("B", "sell", "rank_below_keep", 57), "OSCR": _d("OSCR", "buy", "approved_3_of_3", 2)}
    text = orders.message(exec_day="2026-10-20", close_cst="14:00", prices_day="2026-10-19", book_label="Capa 4",
                          fills=fills, decisions=decisions, vetoed=[_d("PBF", "veto", "vetoed_2_of_3")], flow=500.0,
                          cash_after=1234.0, holdings={"OSCR": (3.0, 1870.0), "DAR": (1.0, 1096.0)},
                          capa2_extra=["PBF"], keep_rank=40, simulated=True)
    assert text.startswith("VENTA B · COMPRA OSCR, DAR — martes 20 oct\nCapa 4 · simulación")
    assert "Depósito de hoy: 500 MXN" in text
    assert "• 2 B ≈ 1,500 MXN · salió del lugar 40 (va en el 57)" in text
    assert "• 3 OSCR ≈ 1,870 MXN (33.66 USD c/u) · nueva, lugar 2, comité aprueba 3 de 3" in text
    assert "• 1 DAR ≈ 1,096 MXN (59.22 USD c/u) · más de una posición que ya tienes" in text
    assert "Vetadas por el comité (no comprar): PBF (2 de 3 modelos)" in text
    assert "Capa 2 sin veto compraría además: PBF" in text
    assert "Después: 2 posiciones" in text


def test_message_holds_when_nothing_trades():
    text = orders.message(exec_day="2026-10-22", close_cst="14:00", prices_day="2026-10-21", book_label="Una acción",
                          fills=[], decisions={"OSCR": _d("OSCR", "hold", "rank_within_keep", 1)}, vetoed=[],
                          flow=0.0, cash_after=76.0, holdings={"OSCR": (58.0, 36822.0)}, capa2_extra=[],
                          keep_rank=1, simulated=False)
    assert text == ("HOLD OSCR — jueves 22 oct\nUna acción\n\n"
                    "Mantén tus 58 OSCR (≈ 36,822 MXN); sigue en el lugar 1.\n\nDatos del cierre del miércoles 21 oct.")
    swap = [{"side": "sell", "ticker": "OSCR", "shares": 58.0, "amount_mxn": 36822.0, "price_usd": 34.0},
            {"side": "buy", "ticker": "TXG", "shares": 90.0, "amount_mxn": 36500.0, "price_usd": 22.0}]
    assert orders.headline(swap, {"TXG": (90.0, 36500.0)}) == "VENTA OSCR · COMPRA TXG"


def test_trading_days_and_close():
    assert next_trading_day("2026-10-23", set()) == "2026-10-26"                    # Friday -> Monday
    assert next_trading_day("2026-11-25", {"2026-11-26"}) == "2026-11-27"           # Thanksgiving
    assert close_utc("2026-10-20").isoformat() == "2026-10-20T20:00:00+00:00"       # EDT: 14:00 CST
    assert close_utc("2026-11-02").isoformat() == "2026-11-02T21:00:00+00:00"       # EST: 15:00 CST
