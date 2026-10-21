"""The investor's orders for the next trading day, sent to them (settings in
config/order_alerts.yaml, rules in src/transforms/orders.py).

After the books are updated for the latest close, it plays the next trading day of
the book the investor follows (by default Capa 4 with the majority veto): the
pending signal's sells and buys, and what the day's deposit buys, in whole SIC
shares and MXN. It posts the list to Slack only when there is something to do,
once per day; a different list later the same day (the committee finished late)
goes out as a correction. While a new
buy still waits for its committee review it holds the list until
wait_for_committee_until_utc. What was sent is kept in order_alerts.
Runs after committee_portfolio and on a morning schedule.
  python -m src.jobs.order_alerts [--dry-run]
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
BOOK_LABELS = {"capa4_mayoria": "Capa 4 · mayoría (Capa 2 con veto de 2 de 3 modelos)",
               "forward_top20": "Capa 2 · factores, sin veto"}


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
    """Post to Slack (ORDERS_WEBHOOK_URL; the workflow falls back to ALERT_WEBHOOK_URL);
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


def main(dry_run: bool = False) -> None:
    cfg = yaml.safe_load((_ROOT / "config/order_alerts.yaml").read_text())
    rules, pcfg = pp.load_rules()
    book, compare = cfg["book"], cfg.get("compare")
    holidays = {str(d) for d in cfg.get("nyse_holidays") or []}
    now = datetime.now(UTC)
    with job_run("order_alerts") as em, pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select max(bar_date) as d from price_bars_daily where ticker = %s", (pcfg["benchmark"],))
            latest_bar = pp._iso(cur.fetchone()["d"])
            cur.execute("select day, cash_mxn, contributed_mxn from paper_nav where portfolio = %s "
                        "order by day desc limit 1", (book,))
            nav = cur.fetchone()
            exec_day = next_trading_day(latest_bar, holidays)
            today = now.astimezone(CST).date().isoformat()
            h, m = (int(x) for x in cfg["wait_for_committee_until_utc"].split(":"))
            morning = lambda d: datetime.fromisoformat(d).replace(hour=h, minute=m, tzinfo=UTC)
            if exec_day < today:
                # the last trading day's prices never came: no list can be trusted (said in the morning,
                # not while the night's ingest may just be late)
                if now < morning(today):
                    return
                sig = f"stale:{latest_bar}"
                if (_last_sent(cur, book, today) or {}).get("signature") == sig:
                    return
                text = (f"Sin lista de órdenes para hoy {orders.day_name(today)}: los últimos precios son del "
                        f"{orders.day_name(latest_bar)} y no llegaron los del {orders.day_name(exec_day)}. "
                        "No operes con una lista vieja; revisa ingest_prices en GitHub Actions.")
                _summary(text)
                em.emit("WARN", "order_alert_stale", text)
                if not dry_run and send(text):
                    _record(cur, book, today, "stale", sig, {}, text)
                    conn.commit()
                return
            if nav and pp._iso(nav["day"]) < latest_bar:
                em.emit("INFO", "order_alert_waiting", f"{book} not updated to {latest_bar} yet")
                return
            if now >= close_utc(exec_day):
                return
            nav_day = pp._iso(nav["day"]) if nav else ""   # no fill yet: everything decided is pending
            cur.execute("select portfolio, signal_day, ticker, action, reason, rank from paper_decisions "
                        "where portfolio = any(%s) and signal_day >= %s", ([book, compare], nav_day or "0001-01-01"))
            pending: dict[str, list[dict]] = {}
            other_buys: set[str] = set()
            for r in cur.fetchall():
                if r["portfolio"] == book:
                    pending.setdefault(pp._iso(r["signal_day"]), []).append(dict(r))
                elif r["action"] == "buy":
                    other_buys.add(r["ticker"])
            deposits = pp.contributions(cur, pcfg["owner"]) + paper.recurring_flows(
                pcfg["forward_start"], exec_day, float(pcfg.get("recurring_mxn") or 0),
                pcfg.get("recurring_days") or [])
            flow = sum(a for d, a in deposits if nav_day < d <= exec_day)
            if not pending and (not flow or not nav):
                em.emit("INFO", "order_alert_none", f"no orders for {exec_day}")
                return
            decisions = {d["ticker"]: d for ds in pending.values() for d in ds}
            waiting = [t for t, d in decisions.items() if d["action"] == "buy" and d["reason"] == "no_review"]
            if waiting and now < morning(exec_day):
                em.emit("INFO", "order_alert_waiting", f"committee hasn't reviewed {', '.join(sorted(waiting))}")
                return

            cur.execute("select ticker, side, shares, amount_mxn, cost_mxn from paper_fills where portfolio = %s "
                        "order by exec_day, side, ticker", (book,))
            state = orders.book_from_fills([dict(r) for r in cur.fetchall()], float(nav["cash_mxn"]) if nav else 0.0,
                                           float(nav["contributed_mxn"]) if nav else 0.0)
            tickers = sorted(set(state.positions) | set(decisions))
            cur.execute("select distinct on (ticker) ticker, close from price_bars_daily where ticker = any(%s) "
                        "and bar_date <= %s and bar_date >= %s::date - 10 and close > 0 "
                        "order by ticker, bar_date desc, ingested_at desc", (tickers, latest_bar, latest_bar))
            close = {r["ticker"]: float(r["close"]) for r in cur.fetchall()}
            cur.execute("select value from macro_observations where series_id = 'DEXMXUS' and value > 0 "
                        "and obs_date <= %s order by obs_date desc, ingested_at desc limit 1", (latest_bar,))
            fx = float(cur.fetchone()["value"])
            fills = orders.preview(state, pending, flow, exec_day, lambda t, d: close.get(t), fx, rules)
            vetoed = [d for d in decisions.values() if d["action"] == "veto"]
            mine = {f["ticker"] for f in fills if f["side"] == "buy"} | set(state.positions)
            capa2_extra = sorted(other_buys - mine) if compare and compare != book else []
            sig = orders.signature(fills, [v["ticker"] for v in vetoed])
            last = _last_sent(cur, book, exec_day)
            if last and last["signature"] == sig:
                return
            bmv = exec_day in {str(d) for d in cfg.get("bmv_holidays") or []}
            text = orders.message(
                exec_day=exec_day, close_cst=close_utc(exec_day).astimezone(CST).strftime("%H:%M"),
                prices_day=latest_bar, book_label=BOOK_LABELS.get(book, book), fills=fills, decisions=decisions,
                vetoed=vetoed, flow=flow, cash_after=state.cash, positions_after=len(state.positions),
                capa2_extra=capa2_extra, keep_rank=rules.keep_rank, simulated=bool(cfg.get("simulated", True)),
                correction=last is not None, bmv_closed=bmv)
            _summary(text)
            payload = {"fills": [{k: f[k] for k in ("side", "ticker", "shares", "price_usd", "amount_mxn", "reason")}
                                 for f in fills], "vetoed": [v["ticker"] for v in vetoed], "flow_mxn": flow,
                       "prices_day": latest_bar, "usd_mxn": fx}
            em.emit("INFO", "order_alert", exec_day, payload={**payload, "dry_run": dry_run})
            if dry_run:
                return
            if not send(text):
                em.emit("WARN", "order_alert_no_channel", "no channel configured: the list is only in the run summary")
                return
            _record(cur, book, exec_day, "correction" if last else "orders", sig, payload, text)
            conn.commit()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv[1:])
