"""The day's action for the investor, every trading day at 08:00 CST: COMPRA, VENTA or
HOLD for the book they follow (config/order_alerts.yaml; rules in src/transforms/orders.py).

With the books updated to the last close, it plays today's close of the book with the
simulation's own rules: the pending signal's sells and buys and what a deposit due
today buys, in whole SIC shares and MXN at the last close; with nothing to trade it is
HOLD with the shares held. Posted to Slack once per trading day (a different result
later that day goes out as a correction); when the last close or the book's update is
missing it says so instead of sending an old action. Sent messages are kept in order_alerts.
  python -m src.jobs.order_alerts [--dry-run | --test]
--test posts a made-up example message (marked PRUEBA) to Slack, to check the webhook.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psycopg2.extras
import yaml

from src.jobs import paper_portfolio as pp
from src.quality.run_metadata import job_run
from src.storage.supabase_client import pg_connection
from src.transforms import orders, paper

_ROOT = Path(__file__).resolve().parents[2]
CST, NY = ZoneInfo("America/Mexico_City"), ZoneInfo("America/New_York")
BOOK_LABELS = {"mi_inversion": "Portafolio de una acción (factor #1) con Mi inversión",
               "desde_enero": "Portafolio de una acción (factor #1) desde enero",
               "capa4_mayoria": "Capa 4 · mayoría (Capa 2 con veto de 2 de 3 modelos)",
               "forward_top20": "Capa 2 · factores, sin veto"}


def source(kind: str, book: str) -> tuple[str, str, tuple]:
    """The tables prefix and row filter of the book: top1_book_* (src/jobs/top1_portfolio.py) and
    paper_* both hold several books in a portfolio column."""
    return ("top1_book" if kind == "top1" else "paper"), " and portfolio = %s", (book,)


def next_trading_day(d: str, holidays: set[str]) -> str:
    x = date.fromisoformat(d)
    while True:
        x = pp._next_trading_day(x)
        if x.isoformat() not in holidays:
            return x.isoformat()


def close_utc(day: str) -> datetime:
    """New York's close (16:00 ET) of `day`, in UTC."""
    return datetime.fromisoformat(day).replace(hour=16, tzinfo=NY).astimezone(UTC)


# ---------- channel ----------

def send(text: str) -> bool:
    """Post to Slack (ORDERS_WEBHOOK_URL: the workflow passes ONE_STOCK_PORTFOLIO_ALERTS_WEBHOOK);
    False with no webhook. A failed post raises, so the run fails and GitHub emails it."""
    url = os.getenv("ORDERS_WEBHOOK_URL")
    if not url:
        return False
    httpx.post(url, json={"text": text}, timeout=20).raise_for_status()
    return True


def _summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("### Órdenes\n\n```\n" + text + "\n```\n\n")
    print(text)


# ---------- the day's orders ----------

def _last_sent(cur, book: str, exec_day: str) -> dict | None:
    cur.execute("select signature, kind from order_alerts where book = %s and exec_day = %s "
                "order by sent_at desc limit 1", (book, exec_day))
    r = cur.fetchone()
    return dict(r) if r else None


def _record(cur, book: str, exec_day: str, kind: str, sig: str, payload: dict, text: str):
    cur.execute("insert into order_alerts (book, exec_day, kind, signature, orders, message) "
                "values (%s, %s, %s, %s, %s, %s)", (book, exec_day, kind, sig, psycopg2.extras.Json(payload), text))


def send_test() -> None:
    """A made-up example of the daily message, to check the Slack webhook and the format."""
    fills = [{"side": "sell", "ticker": "OSCR"}, {"side": "buy", "ticker": "RELY"}]
    usd = {"OSCR": 33.66, "RELY": 21.90}
    swap = orders.message(fills=fills, holdings={"RELY": (90.0, 0.0)}, usd=usd, fx=18.5)
    hold = orders.message(fills=[], holdings={"OSCR": (59.0, 0.0)}, usd=usd, fx=18.5)
    text = f"PRUEBA (datos inventados, no operes con esto)\n{swap}\n{hold}"
    _summary(text)
    if not send(text):
        raise RuntimeError("no Slack webhook configured (secret ONE_STOCK_PORTFOLIO_ALERTS_WEBHOOK)")


def main(dry_run: bool = False) -> None:
    cfg = yaml.safe_load((_ROOT / "config/order_alerts.yaml").read_text())
    rules, pcfg = pp.load_rules()
    rules = paper.Rules(**{**rules.__dict__, **(cfg.get("rules") or {})})
    book = cfg["book"]
    t, w, p = source(cfg.get("source", "top1"), book)
    holidays = {str(d) for d in cfg.get("nyse_holidays") or []}
    now = datetime.now(UTC)
    today = now.astimezone(CST).date().isoformat()
    label = BOOK_LABELS.get(book, book)
    with job_run("order_alerts") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select max(bar_date) as d from price_bars_daily where ticker = %s", (pcfg["benchmark"],))
            latest_bar = pp._iso(cur.fetchone()["d"])
            cur.execute(f"select day, cash_mxn, contributed_mxn from {t}_nav where true{w} "
                        "order by day desc limit 1", p)
            nav = cur.fetchone()
            exec_day = next_trading_day(latest_bar, holidays)
            if exec_day > today:
                em.emit("INFO", "order_alert_none", f"no trading today ({today}); next {exec_day}")
                return
            if now >= close_utc(exec_day):
                em.emit("INFO", "order_alert_none", f"{exec_day}'s close has passed: nothing to send")
                return
            nav_day = pp._iso(nav["day"]) if nav else ""   # no fill yet: everything decided is pending
            first_day = not nav   # the book has not made its first buy: its cash is the deposits up to today

            def deliver(kind: str, sig: str, payload: dict, text: str) -> None:
                last = _last_sent(cur, book, today)
                if last and last["signature"] == sig:
                    return
                if last and last["kind"] != "stale" and kind == "orders":
                    kind = "correction"
                    text = "CORRECCIÓN: cambió lo que te mandé hoy. Esto es lo bueno.\n" + text
                _summary(text)
                em.emit("INFO", "order_alert", today, payload={**payload, "kind": kind, "dry_run": dry_run})
                if dry_run:
                    return
                if not send(text):
                    em.emit("WARN", "order_alert_no_channel", "no Slack webhook: the message is only in the run summary")
                    return
                _record(cur, book, today, kind, sig, payload, text)
                conn.commit()

            if exec_day < today or (nav and nav_day < latest_bar):
                # yesterday's close never came, or the book wasn't updated to it: no action can be trusted
                missing = (f"no llegaron los precios del {orders.day_name(exec_day)}" if exec_day < today
                           else f"{label} no se actualizó con el cierre del {orders.day_name(latest_bar)}")
                deliver("stale", f"stale:{latest_bar}:{nav_day}", {"prices_day": latest_bar, "book_day": nav_day},
                        f"SIN SEÑAL hoy {orders.day_name(today)}: {missing}. No operes con datos viejos; "
                        "revisa GitHub Actions.")
                return

            since = nav_day or "0001-01-01"
            cur.execute(f"select signal_day, ticker, action, reason, rank from {t}_decisions "
                        f"where signal_day >= %s{w}", (since, *p))
            pending: dict[str, list[dict]] = {}
            for r in cur.fetchall():
                pending.setdefault(pp._iso(r.pop("signal_day")), []).append(dict(r))
            # the book's deposits: the plan, plus the investor's recorded ones where the book uses them
            deposits = paper.recurring_flows(pcfg["forward_start"], exec_day, float(pcfg.get("recurring_mxn") or 0),
                                             pcfg.get("recurring_days") or [])
            if cfg.get("recorded_deposits", True):
                deposits += pp.contributions(cur, pcfg["owner"]) or [(pcfg["forward_start"], float(pcfg["initial_mxn"]))]
            flow = sum(a for d, a in deposits if nav_day < d <= exec_day)
            if first_day and not pending:
                # the book's first signal is not stored yet (the night's chain is late): say so, no HOLD without a position
                deliver("stale", f"nosignal:{latest_bar}", {"prices_day": latest_bar},
                        f"SIN SEÑAL hoy {orders.day_name(today)}: {label} aún no tiene su primera decisión "
                        f"con el cierre del {orders.day_name(latest_bar)}. No operes; revisa GitHub Actions.")
                return
            decisions = {d["ticker"]: d for ds in pending.values() for d in ds}

            cur.execute(f"select ticker, side, shares, amount_mxn, cost_mxn from {t}_fills where true{w} "
                        "order by exec_day, side, ticker", p)
            state = orders.book_from_fills([dict(r) for r in cur.fetchall()], float(nav["cash_mxn"]) if nav else 0.0,
                                           float(nav["contributed_mxn"]) if nav else 0.0)
            tickers = sorted(set(state.positions) | set(decisions)) or [pcfg["benchmark"]]
            cur.execute("select distinct on (ticker) ticker, close from price_bars_daily where ticker = any(%s) "
                        "and bar_date <= %s and bar_date >= %s::date - 10 and close > 0 "
                        "order by ticker, bar_date desc, ingested_at desc", (tickers, latest_bar, latest_bar))
            close = {r["ticker"]: float(r["close"]) for r in cur.fetchall()}
            cur.execute("select value from macro_observations where series_id = 'DEXMXUS' and value > 0 "
                        "and obs_date <= %s order by obs_date desc, ingested_at desc limit 1", (latest_bar,))
            fx = float(cur.fetchone()["value"])
            fills = orders.preview(state, pending, flow, exec_day, lambda t, d: close.get(t), fx, rules)
            holdings = {tk: (pos.shares, pos.shares * close.get(tk, 0.0) * fx) for tk, pos in state.positions.items()}
            text = orders.message(fills=fills, holdings=holdings, usd=close, fx=fx)
            payload = {"fills": [{k: f[k] for k in ("side", "ticker", "shares", "price_usd", "amount_mxn", "reason")}
                                 for f in fills], "holdings": sorted(holdings), "flow_mxn": flow, "prices_day": latest_bar, "usd_mxn": fx}
            deliver("orders", orders.signature(fills, []) + ":" + ",".join(sorted(holdings)),
                    payload, text)


if __name__ == "__main__":
    if "--test" in sys.argv[1:]:
        send_test()
    else:
        main(dry_run="--dry-run" in sys.argv[1:])
