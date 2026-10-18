"""MCP tool implementations: read-only views of the Capa 1 catalog for the committee agents.

Docstrings become the tool descriptions agents see, so they say what comes back
and how to read it. Every tool returns the envelope (envelope.py): rows carry a
`_ref` that the agent cites as `<_ref>#<field>`.

Every tool reads "as of" a moment (as_of, default now) so a committee can be
replayed on a past date, and resolve_refs lets a validator (no LLM) look every
cited number up again (see the MCP sketch doc).
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.mcp_server import db
from src.mcp_server.envelope import Envelope, error, parse_as_of, row_ref, rows_with_ref, with_ref

_ROOT = Path(__file__).resolve().parents[2]
_TRADING_DAYS = 252


def _tk(ticker: str) -> str:
    return (ticker or "").strip().upper()


def _known(ticker: str) -> bool:
    return db.one("select 1 as ok from current_tickers where ticker = %s", (ticker,)) is not None


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(n)))


# ---------- price metrics (pure; unit-tested) ----------

def split_adjust(bars: list[dict], splits: list[dict]) -> list[dict]:
    """Divide prices before each split's ex_date by its ratio (volume multiplied),
    so levels and returns are comparable with today's price. Bars ascending.
    Yahoo serves split-adjusted closes, so only bars fetched before the split
    (ingested_at earlier than ex_date) are still raw and get adjusted."""
    out = [dict(b) for b in bars]
    for s in splits:
        ratio = s.get("ratio")
        if not ratio:
            continue
        for b in out:
            if b["bar_date"] < s["ex_date"] and str(b.get("ingested_at") or "")[:10] < s["ex_date"]:
                for c in ("open", "high", "low", "close"):
                    if b.get(c) is not None:
                        b[c] = b[c] / ratio
                if b.get("volume") is not None:
                    b["volume"] = b["volume"] * ratio
    return out


def price_metrics(bars: list[dict]) -> dict[str, Any]:
    """Returns, volatility, drawdown, range position, levels and liquidity from
    ascending daily bars (split-adjusted)."""
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < 2:
        return {}
    last = closes[-1]

    def ret(n: int) -> float | None:
        return _r(last / closes[-1 - n] - 1) if len(closes) > n and closes[-1 - n] else None

    def vol(n: int) -> float | None:
        if len(closes) <= n:
            return None
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))
                if closes[i - 1] > 0 and closes[i] > 0]
        if len(rets) < 2:
            return None
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
        return _r(sd * math.sqrt(_TRADING_DAYS))

    def max_dd(n: int) -> float | None:
        window = closes[-n:]
        peak, worst = window[0], 0.0
        for c in window:
            peak = max(peak, c)
            worst = min(worst, c / peak - 1)
        return _r(worst)

    def hi(n: int) -> float | None:
        v = [b["high"] for b in bars[-n:] if b.get("high") is not None]
        return _r(max(v), 2) if v else None

    def lo(n: int) -> float | None:
        v = [b["low"] for b in bars[-n:] if b.get("low") is not None]
        return _r(min(v), 2) if v else None

    h52, l52 = hi(_TRADING_DAYS), lo(_TRADING_DAYS)
    dv = [b["close"] * b["volume"] for b in bars[-20:] if b.get("close") and b.get("volume")]
    return {
        "last_close": _r(last, 2),
        "last_bar_date": bars[-1]["bar_date"],
        "returns": {"5d": ret(5), "1m": ret(21), "3m": ret(63), "6m": ret(126), "12m": ret(252)},
        "realized_vol_annual": {"20d": vol(20), "60d": vol(60)},
        "max_drawdown": {"3m": max_dd(63), "12m": max_dd(_TRADING_DAYS)},
        "range_52w": {
            "high": h52, "low": l52,
            "position": _r((last - l52) / (h52 - l52)) if h52 and l52 is not None and h52 > l52 else None,
            "pct_from_high": _r(last / h52 - 1) if h52 else None,
        },
        "levels": {"resistance_20d": hi(20), "support_20d": lo(20),
                   "resistance_60d": hi(60), "support_60d": lo(60)},
        "avg_dollar_volume_20d": _r(sum(dv) / len(dv), 0) if dv else None,
        "bars_used": len(closes),
    }


def trend_of(values: list[float | None]) -> dict[str, Any]:
    """Direction of a metric over quarters given newest first: changes up vs down
    across the last 4 quarter-to-quarter steps, and the change vs a year ago."""
    v = [x for x in values[:5] if x is not None]
    if len(v) < 2:
        return {"direction": None}
    steps = [v[i] - v[i + 1] for i in range(len(v) - 1)]
    up, down = sum(s > 0 for s in steps), sum(s < 0 for s in steps)
    direction = "sube" if up > down else "baja" if down > up else "estable"
    yoy = _r(v[0] - v[4]) if len(v) == 5 else None
    return {"direction": direction, "quarters_up": up, "quarters_down": down, "chg_vs_4q_ago": yoy}


def weighted_sentiment(daily: list[dict], days: int, today: date) -> dict[str, Any]:
    """Article-weighted mean of avg_sentiment over the last `days` calendar days."""
    cut = date.fromordinal(today.toordinal() - days + 1).isoformat()
    rows = [d for d in daily if d["date"] >= cut and d.get("n_scored")]
    n = sum(d["n_scored"] for d in rows)
    if not n:
        return {"mean": None, "n_scored": 0}
    return {"mean": _r(sum(d["avg_sentiment"] * d["n_scored"] for d in rows) / n), "n_scored": n}


def round_trip_cost(cfg: dict, amount_mxn: float | None = None, sic: bool = True) -> dict[str, Any]:
    """GBM round-trip cost as a fraction of the amount, from config/cost_model.yaml."""
    cm = cfg["cost_model"]
    rate = cm["commission"]["current_tier_pct"]
    if amount_mxn:
        for tier in cm["commission"]["tiers"]:
            if tier["up_to_mxn"] is None or amount_mxn <= tier["up_to_mxn"]:
                rate = tier["per_side_pct"]
                break
    commission = rate * (1 + cm["iva_pct"])
    fx = cm["fx"]["spread_per_side_pct"] if sic else 0.0
    slippage = cm["slippage_per_side_pct"]
    per_side = commission + fx + slippage
    return {
        "commission_with_iva_per_side": _r(commission, 5),
        "fx_spread_per_side": _r(fx, 5),
        "slippage_per_side": _r(slippage, 5),
        "round_trip": _r(2 * per_side, 5),
        "min_expected_move_3x": _r(6 * per_side, 4),
    }


# ---------- tools ----------
#
# Every tool takes as_of (None = now, or 'YYYY-MM-DD' / an ISO timestamp) and reads
# only what was known then, by each source's own clock:
#   prices and indicators: valid_from (the bar's close); filings and insider trades:
#   filed_at; news: date; ratings, splits, dividends: their dates; street data and
#   fundamentals snapshots: the snapshot date; quarterly fundamentals and EPS
#   surprises: when ingested, or 45 days after the quarter end for older backfills;
#   macro: observation date plus each series' release lag (SQL macro_at).
# Where a row was stored more than once, the version stored by then wins.
# Company profiles, peers and quality flags are not versioned: past reads say so.

_PAST_NOTE = "Lectura al {}: solo datos conocidos en esa fecha."


def _open(tool: str, ticker: str | None, as_of: str | None, check: bool = True) -> tuple[Envelope | None, dict | None]:
    """Envelope for the call, or the error envelope to return (bad as_of, unknown ticker)."""
    t = _tk(ticker) if ticker else None
    try:
        at = parse_as_of(as_of)
    except ValueError as exc:
        return None, error(tool, "bad_as_of", str(exc), t)
    if check and not _known(t):
        return None, error(tool, "unknown_ticker", f"{t} is not in the tracked universe", t, at)
    env = Envelope(tool, t, at)
    if at.past:
        env.note(_PAST_NOTE.format(at.iso))
    return env, None


def _p(env: Envelope, **extra: Any) -> dict[str, Any]:
    """Named query parameters: t (ticker), at (timestamp) and day (date)."""
    return {"t": env.ticker, "at": env.at.ts, "day": env.at.day, **extra}


def _snapshot_at(env: Envelope, cols: str) -> dict | None:
    return db.one(f"select {cols} from fundamentals_snapshot where ticker = %(t)s and as_of <= %(day)s "
                  "order by as_of desc, ingested_at desc limit 1", _p(env))


def get_ticker_overview(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Who the company is and whether its data can be trusted: name, sector,
    industry, benchmark ETF, last close, market cap, beta, and data-quality flags."""
    env, err = _open("get_ticker_overview", ticker, as_of, check=False)
    if err:
        return err
    t = env.ticker
    info = db.one("select ticker, name, exchange, sector, benchmark, tier, enabled from current_tickers "
                  "where ticker = %s", (t,))
    if info is None:
        return error(env.tool, "unknown_ticker", f"{t} is not in the tracked universe", t, env.at)
    env.put("ticker", with_ref(info, "current_tickers", "ticker"))
    prof = db.one("select ticker, name, industry, country, exchange, shares_outstanding, ipo, weburl "
                  "from company_profiles where ticker = %s", (t,))
    env.put("profile", with_ref(prof, "company_profiles", "ticker"))
    env.put("size", with_ref(_snapshot_at(env, "ticker, as_of, market_cap, beta"),
                             "current_fundamentals_snapshot", "ticker", "as_of"))
    bar = db.one("select ticker, bar_date, close, volume from price_bars_daily where ticker = %(t)s "
                 "and valid_from <= %(at)s order by bar_date desc, ingested_at desc limit 1", _p(env))
    env.put("last_bar", with_ref(bar, "current_price_bars", "ticker", "bar_date"))
    if env.at.past:
        env.note("Perfil y banderas de calidad son los de hoy (no tienen historia); se omiten las banderas.")
        return env.out()
    flags = db.query("select ticker, check_name, severity, detail from data_quality_flags "
                     "where ticker = %s", (t,))
    env.put("quality_flags", rows_with_ref(flags, "data_quality_flags", "ticker", "check_name"), required=False)
    if flags:
        env.note("Hay banderas de calidad: menciónalas antes de concluir.")
    return env.out()


def get_price_technicals(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Price and technicals in three windows (5-10 days, 1-3 months, 6-12 months):
    returns, realized volatility, max drawdown, 52-week range position, support and
    resistance, dollar liquidity, plus the latest RSI, MACD, moving averages, ATR,
    Bollinger bands and 21-day relative strength vs the benchmark ETF.
    Prices are split-adjusted, so levels compare with the price on the read date."""
    env, err = _open("get_price_technicals", ticker, as_of)
    if err:
        return err
    t = env.ticker
    bars = db.query("select distinct on (bar_date) bar_date, open, high, low, close, volume, ingested_at "
                    "from price_bars_daily where ticker = %(t)s and valid_from <= %(at)s "
                    "and bar_date >= %(day)s::date - 400 "
                    "order by bar_date, (ingested_at <= %(at)s) desc, ingested_at desc", _p(env))
    splits = db.query("select ex_date, ratio from current_corporate_actions where ticker = %(t)s "
                      "and action_type = 'split' and ratio is not null and ex_date <= %(day)s", _p(env))
    m = price_metrics(split_adjust(bars, splits))
    if m:
        m = {"_ref": row_ref("calc:price_metrics", t, m["last_bar_date"]),
             "_inputs": f"current_price_bars:{t} {bars[0]['bar_date']}..{m['last_bar_date']}, splits applied",
             **m}
    env.put("price", m or None)
    ind = db.one(
        "select ticker, bar_date, rsi_14, macd, macd_signal, macd_hist, sma_20, sma_50, sma_200, "
        "ema_20, ema_50, atr_14, bb_upper, bb_middle, bb_lower, volume_ma_20, rel_strength_21d "
        "from indicators_daily where ticker = %(t)s and valid_from <= %(at)s "
        "order by bar_date desc, ingested_at desc limit 1", _p(env))
    if ind and m:
        last = m["last_close"]
        ind["close_vs_sma_50"] = _r(last / ind["sma_50"] - 1) if ind.get("sma_50") else None
        ind["close_vs_sma_200"] = _r(last / ind["sma_200"] - 1) if ind.get("sma_200") else None
        ind["atr_pct"] = _r(ind["atr_14"] / last) if ind.get("atr_14") else None
        if ind["bar_date"] != m["last_bar_date"]:
            env.note(f"Los indicadores son del {ind['bar_date']} y el último precio del {m['last_bar_date']}.")
    env.put("indicators", with_ref(ind, "indicators_daily", "ticker", "bar_date"))
    if env.at.past and not ind:
        env.note("Los indicadores guardan solo el último año.")
    return env.out()


_TREND_FIELDS = ("eps", "sales_per_share", "gross_margin", "operating_margin", "net_margin",
                 "fcf_margin", "roe_ttm", "debt_to_equity", "current_ratio", "growth_yoy")
_REPORT_LAG_DAYS = 45  # a quarter's numbers are public about this long after it ends


def get_fundamentals(ticker: str, quarters: int = 8, as_of: str | None = None) -> dict[str, Any]:
    """Valuation, growth, margins and balance sheet (TTM snapshot), the last
    quarters (default 8), and the direction of each metric over the last 4 quarters."""
    env, err = _open("get_fundamentals", ticker, as_of)
    if err:
        return err
    t = env.ticker
    snap = _snapshot_at(env, "*")
    if snap:
        for k in ("id", "ingested_at", "valid_from", "run_id"):
            snap.pop(k, None)
    env.put("snapshot", with_ref(snap, "current_fundamentals_snapshot", "ticker", "as_of"))
    if env.at.past and not snap:
        env.note("No hay snapshot de fundamentales en o antes de esa fecha (la historia empieza el 2026-10-17).")
    q = db.query("select distinct on (period_end) ticker, period_end, " + ", ".join(_TREND_FIELDS) +
                 ", pe_ttm, pb, ps_ttm, ingested_at > %(at)s as _late from fundamentals_quarterly "
                 "where ticker = %(t)s and (ingested_at <= %(at)s or period_end + %(lag)s <= %(day)s) "
                 "order by period_end desc, (ingested_at <= %(at)s) desc, ingested_at desc limit %(n)s",
                 _p(env, lag=_REPORT_LAG_DAYS, n=max(_clamp(quarters, 1, 16), 5)))
    # trends always use 5 quarters (4 steps), whatever `quarters` shows, so the same
    # calc ref means the same numbers in every answer
    trends = {f: trend_of([r.get(f) for r in q]) for f in _TREND_FIELDS}
    q = q[:_clamp(quarters, 1, 16)]
    if any(r.pop("_late") for r in q):
        env.note(f"Algunos trimestres se cargaron después; se asume que eran públicos {_REPORT_LAG_DAYS} "
                 "días después del cierre del trimestre.")
    env.put("quarters", rows_with_ref(q, "current_fundamentals", "ticker", "period_end"))
    if q:
        env.put("trends", {
            "_ref": row_ref("calc:quarterly_trend", t, q[0]["period_end"]),
            **trends,
        })
    return env.out()


def get_peer_valuation(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Valuation, margins, growth and ROE next to the median of the company's
    industry peers, with the ratio company/peer for each metric."""
    env, err = _open("get_peer_valuation", ticker, as_of)
    if err:
        return err
    t = env.ticker
    row = db.one("select * from peer_comparison_at(%(t)s, %(at)s)", _p(env))
    if row:
        rel = {}
        for k in ("pe_ttm", "ps_ttm", "pb", "gross_margin_ttm", "operating_margin_ttm",
                  "revenue_growth_ttm_yoy", "roe_ttm"):
            mine, peer = row.get(k), row.get(f"peer_{k}")
            rel[k] = _r(mine / peer) if mine is not None and peer not in (None, 0) else None
        row["ratio_vs_peers"] = rel
        if not row.get("n_peers"):
            env.missing.append("peer_medians")
    env.put("comparison", with_ref(row, "peer_comparison", "ticker"))
    peers = db.one("select ticker, peers from company_profiles where ticker = %s", (t,))
    env.put("peers", with_ref(peers, "company_profiles", "ticker"), required=False)
    if env.at.past:
        env.note("La lista de pares es la de hoy; sus métricas son las de esa fecha.")
    return env.out()


def get_analyst_view(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """What sell-side analysts think and how it is changing: rating counts and a
    1-5 score with its 3-month change, price targets and upside from the last close,
    EPS estimate revisions over 7/30/90 days per fiscal period, and recent
    upgrades/downgrades (last 90 days)."""
    env, err = _open("get_analyst_view", ticker, as_of)
    if err:
        return err
    env.put("consensus", with_ref(db.one("select * from analyst_consensus_at(%(t)s, %(at)s)", _p(env)),
                                  "analyst_consensus", "ticker", "period"))
    tgt = db.one("select ticker, snapshot_date, close_date, last_close, target_low, target_mean, "
                 "target_median, target_high, n_target_analysts, target_upside, recommendation_mean "
                 "from street_at(%(t)s, %(at)s)", _p(env))
    env.put("price_targets", with_ref(tgt, "street_latest", "ticker", "snapshot_date"))
    rev = db.query("select * from estimate_revisions_at(%(t)s, %(at)s) "
                   "order by array_position(array['0q','+1q','0y','+1y'], period)", _p(env))
    env.put("estimate_revisions", rows_with_ref(rev, "estimate_revisions", "ticker", "period", "snapshot_date"))
    if env.at.past and not (tgt or rev):
        env.note("Los objetivos de precio y revisiones de Yahoo empiezan el 2026-10-18.")
    ch = db.query("select ticker, grade_date, firm, action, from_grade, to_grade, price_target_action, "
                  "prior_price_target, current_price_target from analyst_rating_changes "
                  "where ticker = %(t)s and grade_date <= %(day)s and grade_date > %(day)s::date - 90 "
                  "order by grade_date desc", _p(env))
    counts: dict[str, int] = {}
    for c in ch:
        counts[c["action"] or "?"] = counts.get(c["action"] or "?", 0) + 1
    env.put("rating_changes_90d", {"counts_by_action": counts,
                                   "latest": rows_with_ref(ch[:15], "analyst_rating_changes",
                                                           "ticker", "grade_date", "firm")},
            required=False)
    return env.out()


def get_short_interest(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Short sellers' positioning: shares short, % of float, days to cover, and the
    change against the prior month (exchange data published twice a month)."""
    env, err = _open("get_short_interest", ticker, as_of)
    if err:
        return err
    row = db.one("select ticker, snapshot_date, short_interest_date, shares_short, short_pct_float, "
                 "short_ratio, short_chg_1m, float_shares from street_at(%(t)s, %(at)s)", _p(env))
    env.put("short_interest", with_ref(row, "street_latest", "ticker", "snapshot_date"))
    if env.at.past and not row:
        env.note("El interés en corto de Yahoo empieza el 2026-10-18.")
    return env.out()


def get_earnings(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Next earnings report (date, before/after market, EPS and revenue estimates),
    the last reported quarter, and the last 8 EPS surprises."""
    env, err = _open("get_earnings", ticker, as_of)
    if err:
        return err
    day = env.at.day.isoformat()
    cal = db.query("select distinct on (year, quarter) ticker, event_date, hour, quarter, year, eps_estimate, "
                   "eps_actual, revenue_estimate, revenue_actual from raw_earnings_calendar where ticker = %(t)s "
                   "order by year, quarter, (ingested_at <= %(at)s) desc, ingested_at desc", _p(env))
    upcoming = sorted((c for c in cal if c["event_date"] >= day), key=lambda c: c["event_date"])
    reported = sorted((c for c in cal if c["event_date"] < day), key=lambda c: c["event_date"])
    nxt = upcoming[0] if upcoming else None
    if nxt:
        nxt["eps_actual"] = nxt["revenue_actual"] = None  # not reported yet on the read date
        nxt["days_to_report"] = (date.fromisoformat(nxt["event_date"]) - env.at.day).days
    env.put("next_report", with_ref(nxt, "current_earnings_calendar", "ticker", "event_date"), required=False)
    env.put("last_report", with_ref(reported[-1] if reported else None, "current_earnings_calendar",
                                    "ticker", "event_date"), required=False)
    s = db.query("select distinct on (period) ticker, period, actual, estimate, surprise, surprise_pct "
                 "from raw_earnings_surprises where ticker = %(t)s "
                 "and (ingested_at <= %(at)s or period + %(lag)s <= %(day)s) "
                 "order by period desc, (ingested_at <= %(at)s) desc, ingested_at desc limit 8",
                 _p(env, lag=_REPORT_LAG_DAYS))
    env.put("surprises", rows_with_ref(s, "current_earnings_surprises", "ticker", "period"))
    return env.out()


def get_news_sentiment(ticker: str, days: int = 90, as_of: str | None = None) -> dict[str, Any]:
    """News tone from FinBERT (-1 negative to +1 positive): article-weighted means
    over 1, 3, 7 and 30 days with article counts, plus the daily series. Few
    articles mean a weak signal."""
    env, err = _open("get_news_sentiment", ticker, as_of)
    if err:
        return err
    t = env.ticker
    daily = db.query("select ticker, date, n_articles, n_scored, avg_sentiment, n_positive, n_negative "
                     "from news_sentiment_daily where ticker = %(t)s and date <= %(day)s "
                     "and date > %(day)s::date - %(n)s order by date", _p(env, n=_clamp(days, 7, 365)))
    day = env.at.day
    env.put("windows", {"_ref": row_ref("calc:weighted_sentiment", t, day.isoformat()),
                        **{f"{d}d": weighted_sentiment(daily, d, day) for d in (1, 3, 7, 30)}}
            if daily else None)
    env.put("daily", rows_with_ref(daily, "news_sentiment_daily", "ticker", "date"))
    return env.out()


def get_filing_events(ticker: str, days: int = 90, only_flags: bool = False,
                      as_of: str | None = None) -> dict[str, Any]:
    """8-K and 6-K events from SEC EDGAR with their items and flags: is_results
    (2.02), is_red_flag (bankruptcy, debt acceleration, impairment, delisting notice,
    non-reliance), is_leadership_change (5.02), and the press-release sentiment."""
    env, err = _open("get_filing_events", ticker, as_of)
    if err:
        return err
    flt = " and (is_results or is_red_flag or is_leadership_change)" if only_flags else ""
    rows = db.query("select id, ticker, form_type, filed_at, url, items, sentiment, is_results, is_red_flag, "
                    f"is_leadership_change from filing_events where ticker = %(t)s{flt} "
                    "and filed_at <= %(at)s and filed_at > %(at)s - make_interval(days => %(n)s) "
                    "order by filed_at desc limit 30", _p(env, n=_clamp(days, 1, 730)))
    env.put("events", rows_with_ref(rows, "filing_events", "id"), required=False)
    if any(r.get("is_red_flag") for r in rows):
        env.note("Hay al menos un 8-K de alerta en la ventana.")
    return env.out()


def get_insider_activity(ticker: str, days: int = 90, as_of: str | None = None) -> dict[str, Any]:
    """Insider buying and selling: the 30-day summary (open-market buys vs
    discretionary sells vs 10b5-1 plan sells), recent Form 4 trades with role, and
    Finnhub's monthly MSPR (-100 to +100) for the last 12 months. Plan sales say
    little; buys and discretionary sells are the signal."""
    env, err = _open("get_insider_activity", ticker, as_of)
    if err:
        return err
    env.put("summary_30d", with_ref(db.one("select * from insider_activity_30d_at(%(t)s, %(at)s)", _p(env)),
                                    "insider_activity_30d", "ticker"), required=False)
    trades = db.query("select id, ticker, filed_at, owner, role, is_10b5_1, codes, buy_value, sell_value, "
                      "net_shares from insider_trades where ticker = %(t)s and filed_at <= %(at)s "
                      "and filed_at > %(at)s - make_interval(days => %(n)s) order by filed_at desc limit 25",
                      _p(env, n=_clamp(days, 1, 365)))
    env.put("trades", rows_with_ref(trades, "insider_trades", "id"), required=False)
    # a month's MSPR is complete (and comparable) once the month is over
    mspr = db.query("select distinct on (year, month) ticker, year, month, mspr, change "
                    "from raw_insider_sentiment where ticker = %(t)s "
                    "and make_date(year, month, 1) + interval '1 month' <= %(day)s "
                    "order by year desc, month desc, valid_from desc limit 12", _p(env))
    env.put("mspr_monthly", rows_with_ref(mspr, "raw_insider_sentiment", "ticker", "year", "month"))
    return env.out()


def get_catalysts(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """What is coming and what just happened: days to earnings with estimates,
    recent surprises and beats, counts of 8-K / Form 4 / Form 144 in 30 days,
    dividends and splits."""
    env, err = _open("get_catalysts", ticker, as_of)
    if err:
        return err
    env.put("catalysts", with_ref(db.one("select * from catalysts_at(%(t)s, %(at)s)", _p(env)),
                                  "catalysts", "ticker"))
    return env.out()


def get_macro(series: list[str] | None = None, history_days: int = 0, as_of: str | None = None) -> dict[str, Any]:
    """US macro context from FRED with changes over 1, 3 and 12 months: fed funds,
    3-month/2-year/10-year yields, 10y-2y curve, CPI, unemployment, jobless claims,
    VIX, high-yield spread, WTI oil and USD/MXN (DEXMXUS). Optionally the daily
    history of chosen series. Each value is the last one published by the read date."""
    env, err = _open("get_macro", None, as_of, check=False)
    if err:
        return err
    sel = [s.upper() for s in series] if series else None
    latest = db.query("select * from macro_at(%(at)s, %(sel)s)", _p(env, sel=sel))
    env.put("latest", rows_with_ref(latest, "macro_latest", "series_id", "obs_date"))
    if history_days and sel:
        hist = db.query("select series_id, obs_date, value from macro_observations where series_id = any(%(sel)s) "
                        "and obs_date <= %(day)s and obs_date >= %(day)s::date - %(n)s order by series_id, obs_date",
                        _p(env, sel=sel, n=_clamp(history_days, 1, 730)))
        env.put("history", rows_with_ref(hist, "macro_observations", "series_id", "obs_date"), required=False)
    return env.out()


_SOURCE_JOBS = ("ingest_prices", "compute_indicators", "ingest_news", "ingest_filings", "enrich_filings",
                "ingest_fundamentals", "ingest_analysts", "ingest_street", "ingest_earnings",
                "ingest_earncal", "ingest_insider_sentiment", "ingest_macro", "ingest_profiles")


def get_data_status(ticker: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    """Can the data be trusted: per-ticker quality flags (stale prices, bad bars,
    unexplained jumps, stale fundamentals) and the last run of each source pipeline
    with its status."""
    env, err = _open("get_data_status", ticker, as_of, check=False)
    if err:
        return err
    if env.ticker and not env.at.past:
        flags = db.query("select ticker, check_name, severity, detail from data_quality_flags "
                         "where ticker = %s", (env.ticker,))
        env.put("quality_flags", rows_with_ref(flags, "data_quality_flags", "ticker", "check_name"), required=False)
    runs = db.query("select distinct on (job_name) run_id, job_name, status, started_at, ended_at, rows_written "
                    "from job_runs where job_name = any(%(jobs)s) and started_at <= %(at)s "
                    "order by job_name, started_at desc", _p(env, jobs=list(_SOURCE_JOBS)))
    # one workflow run (run_id) can hold several jobs, so the job name is part of the key
    env.put("last_runs", rows_with_ref(runs, "job_runs", "run_id", "job_name"))
    bad = [r["job_name"] for r in runs if r["status"] in ("failed", "abandoned")]
    if bad:
        env.note("Fuentes con la última corrida fallida: " + ", ".join(bad))
    if env.at.past:
        env.note("Las banderas de calidad solo existen para hoy; el estado de corridas es el de esa fecha.")
    return env.out()


def get_cost_model(amount_mxn: float | None = None, market: str = "SIC", as_of: str | None = None) -> dict[str, Any]:
    """GBM trading costs for a round trip (buy + sell) as a fraction of the amount:
    commission with IVA, FX spread (SIC only) and slippage, and the minimum
    expected move worth a buy (3x the round trip). Includes the USD/MXN rate."""
    env, err = _open("get_cost_model", None, as_of, check=False)
    if err:
        return err
    text = (_ROOT / "config/cost_model.yaml").read_text()
    costs = round_trip_cost(yaml.safe_load(text), amount_mxn, sic=market.upper() == "SIC")
    env.put("costs", {"_ref": row_ref("config", "cost_model.yaml"), "market": market.upper(),
                      "amount_mxn": amount_mxn, **costs})
    if market.upper() == "SIC" and "PLACEHOLDER" in text:
        env.note("El spread de FX es un estimado; falta verificarlo en un estado de cuenta de GBM.")
    fx = db.one("select series_id, obs_date, value from macro_at(%(at)s, array['DEXMXUS'])", _p(env))
    env.put("usd_mxn", with_ref(fx, "macro_latest", "series_id", "obs_date"), required=False)
    return env.out()


def get_research_digest(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Chair only: the whole ticker in one compact answer (overview, technicals,
    fundamentals, peers, analysts, short interest, earnings, news tone, filings,
    insiders, catalysts and data status). Members must use their own tools."""
    env, err = _open("get_research_digest", ticker, as_of)
    if err:
        return err
    t, a = env.ticker, as_of
    parts = {
        "overview": get_ticker_overview(t, as_of=a), "technicals": get_price_technicals(t, as_of=a),
        "fundamentals": get_fundamentals(t, quarters=4, as_of=a), "peers": get_peer_valuation(t, as_of=a),
        "analysts": get_analyst_view(t, as_of=a), "short_interest": get_short_interest(t, as_of=a),
        "earnings": get_earnings(t, as_of=a), "news_sentiment": get_news_sentiment(t, days=30, as_of=a),
        "filings": get_filing_events(t, days=60, only_flags=True, as_of=a),
        "insiders": get_insider_activity(t, days=60, as_of=a), "catalysts": get_catalysts(t, as_of=a),
    }
    for name, res in parts.items():
        data = dict(res.get("data") or {})
        data.pop("daily", None)  # the chair gets the windows, not the series
        env.put(name, data)
        env.missing += [f"{name}.{m}" for m in (res.get("data_status") or {}).get("missing", [])]
        env.notes += [n for n in res.get("notes", []) if n not in env.notes]
    return env.out()


_FACTOR_COLS = ("ticker, as_of, industry, eligible, rank, score, n_factors, z_momentum, z_revisions, z_pead, "
                "z_insider, z_quality")
_FACTOR_INPUTS = ("adv_20d, mom_12_1, rev_chg, rev_breadth, sue, ear, ins_buyers, ins_buy_value, roe_ttm, "
                  "gross_margin, debt_to_equity, op_margin_std, last_bar_date")
_FACTOR_HELP = ("z-scores within the industry (0 = average, +1 = one standard deviation better); "
                "score = mean of the five, missing factor = 0; rank 1 = best of the day.")


def _factor_day(env: Envelope) -> str | None:
    row = db.one("select max(as_of) as d from factor_scores where as_of <= %(day)s", _p(env))
    return row["d"] if row else None


def screen_universe(top_n: int = 30, industry: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    """The stock screen: the best-ranked eligible stocks (fresh price, >= 1M USD a
    day traded) by the five-factor score (momentum 12-1, EPS estimate revisions,
    post-earnings drift, insider buying, quality), with each factor's z-score.
    Optionally one industry. These are candidates for the committee, not picks."""
    env, err = _open("screen_universe", None, as_of, check=False)
    if err:
        return err
    day = _factor_day(env)
    rows = db.query(f"select {_FACTOR_COLS} from factor_scores where as_of = %(d)s and eligible "
                    "and (%(ind)s::text is null or industry = %(ind)s) order by rank limit %(n)s",
                    _p(env, d=day, ind=industry, n=_clamp(top_n, 1, 200))) if day else []
    env.put("candidates", rows_with_ref(rows, "factor_scores", "ticker", "as_of"))
    if day:
        n = db.one("select count(*) filter (where eligible) as eligible, count(*) as total "
                   "from factor_scores where as_of = %(d)s", _p(env, d=day))
        env.put("universe", {"as_of": day, **n}, required=False)
    env.note(_FACTOR_HELP)
    return env.out()


def get_factor_scores(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """One stock's five-factor score and rank, the raw inputs behind each factor
    (12-1 return, estimate change and breadth, last surprise and price reaction,
    insider buyers, ROE, margins, debt, margin stability) and its rank over the
    last month-ends."""
    env, err = _open("get_factor_scores", ticker, as_of)
    if err:
        return err
    day = _factor_day(env)
    row = db.one(f"select {_FACTOR_COLS}, {_FACTOR_INPUTS} from factor_scores where ticker = %(t)s "
                 "and as_of = %(d)s", _p(env, d=day)) if day else None
    env.put("scores", with_ref(row, "factor_scores", "ticker", "as_of"))
    hist = db.query("select distinct on (date_trunc('month', as_of)) ticker, as_of, rank, score "
                    "from factor_scores where ticker = %(t)s and as_of <= %(day)s "
                    "order by date_trunc('month', as_of) desc, as_of desc limit 12", _p(env))
    env.put("monthly_history", rows_with_ref(hist, "factor_scores", "ticker", "as_of"), required=False)
    if row and not row["eligible"]:
        env.note("No es elegible ese día (precio viejo o menos de 1 millón de dólares operados al día).")
    env.note(_FACTOR_HELP)
    return env.out()


# ---------- validator ----------
#
# resolve_refs looks every cited number up again, with no LLM: it re-runs the tool
# that serves the ref's view for that ticker at the same as_of (so derived fields
# such as ratio_vs_peers or returns.5d resolve too) and reads the field from the
# row carrying that exact _ref. Tools run with their widest windows, once per
# ticker, so a row the agent saw with a narrower window is still found.

_VIEW_TOOLS: dict[str, tuple[str, ...]] = {
    "current_tickers": ("get_ticker_overview",),
    "company_profiles": ("get_ticker_overview", "get_peer_valuation"),
    "current_fundamentals_snapshot": ("get_fundamentals", "get_ticker_overview"),
    "current_price_bars": ("get_ticker_overview",),
    "data_quality_flags": ("get_ticker_overview",),
    "calc:price_metrics": ("get_price_technicals",),
    "indicators_daily": ("get_price_technicals",),
    "current_fundamentals": ("get_fundamentals",),
    "calc:quarterly_trend": ("get_fundamentals",),
    "peer_comparison": ("get_peer_valuation",),
    "analyst_consensus": ("get_analyst_view",),
    "street_latest": ("get_analyst_view", "get_short_interest"),
    "estimate_revisions": ("get_analyst_view",),
    "analyst_rating_changes": ("get_analyst_view",),
    "current_earnings_calendar": ("get_earnings",),
    "current_earnings_surprises": ("get_earnings",),
    "news_sentiment_daily": ("get_news_sentiment",),
    "calc:weighted_sentiment": ("get_news_sentiment",),
    "filing_events": ("get_filing_events",),
    "insider_activity_30d": ("get_insider_activity",),
    "insider_trades": ("get_insider_activity",),
    "raw_insider_sentiment": ("get_insider_activity",),
    "catalysts": ("get_catalysts",),
    "macro_latest": ("get_macro",),
    "macro_observations": ("get_macro",),
    "job_runs": ("get_data_status",),
    "config": ("get_cost_model",),
    "factor_scores": ("get_factor_scores",),
}

# widest windows, so whatever the agent asked for is a subset
_WIDE: dict[str, dict[str, Any]] = {
    "get_fundamentals": {"quarters": 16},
    "get_news_sentiment": {"days": 365},
    "get_filing_events": {"days": 730},
    "get_insider_activity": {"days": 365},
}


def parse_ref(cite: str) -> tuple[str, str, str | None]:
    """'view:key1|key2#field.sub' -> (view, key, field). calc refs keep their prefix."""
    ref, _, field = cite.partition("#")
    prefix = ""
    if ref.startswith("calc:"):
        prefix, ref = "calc:", ref[5:]
    view, sep, key = ref.partition(":")
    if not sep or not view or not key:
        raise ValueError(f"not a ref: {cite!r}")
    return prefix + view, key, field or None


def _field(row: dict, path: str) -> tuple[bool, Any]:
    cur: Any = row
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _index_refs(node: Any, out: dict[str, dict]) -> None:
    """Every dict carrying a _ref anywhere in a tool answer, merged by ref."""
    if isinstance(node, dict):
        if "_ref" in node:
            out.setdefault(node["_ref"], {}).update(node)
        for v in node.values():
            _index_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _index_refs(v, out)


def _ticker_for(view: str, key: str) -> str | None:
    """Subject of a ref: the ticker for most views, series for macro, none for global ones."""
    if view in ("job_runs", "config"):
        return None
    if view in ("filing_events", "insider_trades"):
        row = db.one(f"select ticker from {view} where id::text = %s", (key,))
        return row["ticker"] if row else None
    return key.split("|", 1)[0]


def _matches(claimed: Any, actual: Any, rel_tol: float) -> bool:
    if isinstance(claimed, (int, float)) and isinstance(actual, (int, float)) \
            and not isinstance(claimed, bool) and not isinstance(actual, bool):
        # agents may quote a fraction as a percent (0.041 -> 4.1)
        return any(abs(claimed - a) <= max(1e-9, rel_tol * abs(a)) for a in (actual, actual * 100))
    return str(claimed).strip().lower() == str(actual).strip().lower()


def resolve_refs(refs: list[Any], as_of: str | None = None, rel_tol: float = 0.005) -> dict[str, Any]:
    """Validator only: look up cited numbers again. Each item is a cite string
    '<_ref>#<field>' or {"cite": ..., "value": claimed}. Returns the stored value
    per cite and, when a claimed value is given, whether it matches (relative
    tolerance rel_tol, percent form of a fraction accepted). Use the same as_of
    the agents ran with (the envelope's as_of; omit for a live run)."""
    env, err = _open("resolve_refs", None, as_of, check=False)
    if err:
        return err
    cache: dict[tuple[str, str | None], dict[str, dict]] = {}
    results = []
    for item in refs or []:
        cite, claimed = (item.get("cite"), item.get("value")) if isinstance(item, dict) else (item, None)
        res: dict[str, Any] = {"cite": cite}
        try:
            view, key, field = parse_ref(str(cite))
        except ValueError as exc:
            results.append({**res, "status": "bad_ref", "detail": str(exc)})
            continue
        if view not in _VIEW_TOOLS:
            results.append({**res, "status": "unknown_view"})
            continue
        subject = _ticker_for(view, key)
        ref = cite.partition("#")[0]
        row: dict | None = None
        for tool_name in _VIEW_TOOLS[view]:  # a view served by two tools may split its fields
            ck = (tool_name, subject)
            if ck not in cache:
                kwargs = dict(_WIDE.get(tool_name, {}), as_of=as_of)
                if tool_name == "get_macro":
                    out = ALL_TOOLS[tool_name]([subject], history_days=730, **kwargs)
                elif tool_name in ("get_data_status", "get_cost_model"):
                    out = ALL_TOOLS[tool_name](**kwargs)
                else:
                    out = ALL_TOOLS[tool_name](subject, **kwargs)
                idx: dict[str, dict] = {}
                _index_refs(out.get("data"), idx)
                cache[ck] = idx
            part = cache[ck].get(ref)
            if part is not None:
                row = {**(row or {}), **part}
                if not field or _field(row, field)[0]:
                    break
        if row is None:
            results.append({**res, "status": "ref_not_found"})
            continue
        if not field:
            results.append({**res, "status": "row_found"})
            continue
        found, value = _field(row, field)
        if not found:
            results.append({**res, "status": "field_not_found"})
            continue
        res.update(status="found", value=value)
        if claimed is not None:
            res["claimed"] = claimed
            res["match"] = value is not None and _matches(claimed, value, rel_tol)
        results.append(res)
    checked = [r for r in results if "match" in r]
    env.put("results", results)
    env.put("summary", {"n": len(results), "found": sum(r["status"] == "found" for r in results),
                        "checked": len(checked), "mismatches": sum(not r["match"] for r in checked),
                        "unresolved": sum(r["status"] not in ("found", "row_found") for r in results)},
            required=False)
    return env.out()


ALL_TOOLS = {f.__name__: f for f in (
    get_ticker_overview, get_price_technicals, get_fundamentals, get_peer_valuation, get_analyst_view,
    get_short_interest, get_earnings, get_news_sentiment, get_filing_events, get_insider_activity,
    get_catalysts, get_macro, get_data_status, get_cost_model, get_research_digest,
    screen_universe, get_factor_scores, resolve_refs,
)}
