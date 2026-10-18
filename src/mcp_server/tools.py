"""MCP tool implementations: read-only views of the Capa 1 catalog for the committee agents.

Docstrings become the tool descriptions agents see, so they say what comes back
and how to read it. Every tool returns the envelope (envelope.py): rows carry a
`_ref` that the agent cites as `<_ref>#<field>`.

Phase 1 reads the latest data. Point-in-time reads (as_of) and resolve_refs for
the validator come in phase 2 (see the MCP sketch doc).
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.mcp_server import db
from src.mcp_server.envelope import Envelope, error, row_ref, rows_with_ref, with_ref

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


def _unknown(tool: str, ticker: str) -> dict:
    return error(tool, "unknown_ticker", f"{ticker} is not in the tracked universe", ticker)


# ---------- price metrics (pure; unit-tested) ----------

def split_adjust(bars: list[dict], splits: list[dict]) -> list[dict]:
    """Divide prices before each split's ex_date by its ratio (volume multiplied),
    so levels and returns are comparable with today's price. Bars ascending."""
    out = [dict(b) for b in bars]
    for s in splits:
        ratio = s.get("ratio")
        if not ratio:
            continue
        for b in out:
            if b["bar_date"] < s["ex_date"]:
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

def get_ticker_overview(ticker: str) -> dict[str, Any]:
    """Who the company is and whether its data can be trusted today: name, sector,
    industry, benchmark ETF, last close, market cap, beta, and data-quality flags."""
    t = _tk(ticker)
    env = Envelope("get_ticker_overview", t)
    info = db.one("select ticker, name, exchange, sector, benchmark, tier, enabled from current_tickers "
                  "where ticker = %s", (t,))
    if info is None:
        return _unknown(env.tool, t)
    env.put("ticker", with_ref(info, "current_tickers", "ticker"))
    prof = db.one("select ticker, name, industry, country, exchange, shares_outstanding, ipo, weburl "
                  "from company_profiles where ticker = %s", (t,))
    env.put("profile", with_ref(prof, "company_profiles", "ticker"))
    snap = db.one("select ticker, as_of, market_cap, beta from current_fundamentals_snapshot "
                  "where ticker = %s", (t,))
    env.put("size", with_ref(snap, "current_fundamentals_snapshot", "ticker", "as_of"))
    bar = db.one("select ticker, bar_date, close, volume from price_bars_daily where ticker = %s "
                 "order by bar_date desc, ingested_at desc limit 1", (t,))
    env.put("last_bar", with_ref(bar, "current_price_bars", "ticker", "bar_date"))
    flags = db.query("select ticker, check_name, severity, detail from data_quality_flags "
                     "where ticker = %s", (t,))
    env.put("quality_flags", rows_with_ref(flags, "data_quality_flags", "ticker", "check_name"), required=False)
    if flags:
        env.note("Hay banderas de calidad: menciónalas antes de concluir.")
    return env.out()


def get_price_technicals(ticker: str) -> dict[str, Any]:
    """Price and technicals in three windows (5-10 days, 1-3 months, 6-12 months):
    returns, realized volatility, max drawdown, 52-week range position, support and
    resistance, dollar liquidity, plus the latest RSI, MACD, moving averages, ATR,
    Bollinger bands and 21-day relative strength vs the benchmark ETF.
    Prices are split-adjusted, so levels compare with today's price."""
    t = _tk(ticker)
    env = Envelope("get_price_technicals", t)
    if not _known(t):
        return _unknown(env.tool, t)
    bars = db.query("select bar_date, open, high, low, close, volume from current_price_bars "
                    "where ticker = %s and bar_date >= current_date - 400 order by bar_date", (t,))
    splits = db.query("select ex_date, ratio from current_corporate_actions where ticker = %s "
                      "and action_type = 'split' and ratio is not null", (t,))
    m = price_metrics(split_adjust(bars, splits))
    if m:
        m = {"_ref": row_ref("calc:price_metrics", t, m["last_bar_date"]),
             "_inputs": f"current_price_bars:{t} {bars[0]['bar_date']}..{m['last_bar_date']}, splits applied",
             **m}
    env.put("price", m or None)
    ind = db.one(
        "select ticker, bar_date, rsi_14, macd, macd_signal, macd_hist, sma_20, sma_50, sma_200, "
        "ema_20, ema_50, atr_14, bb_upper, bb_middle, bb_lower, volume_ma_20, rel_strength_21d "
        "from indicators_daily where ticker = %s order by bar_date desc, ingested_at desc limit 1", (t,))
    if ind and m:
        last = m["last_close"]
        ind["close_vs_sma_50"] = _r(last / ind["sma_50"] - 1) if ind.get("sma_50") else None
        ind["close_vs_sma_200"] = _r(last / ind["sma_200"] - 1) if ind.get("sma_200") else None
        ind["atr_pct"] = _r(ind["atr_14"] / last) if ind.get("atr_14") else None
        if ind["bar_date"] != m["last_bar_date"]:
            env.note(f"Los indicadores son del {ind['bar_date']} y el último precio del {m['last_bar_date']}.")
    env.put("indicators", with_ref(ind, "indicators_daily", "ticker", "bar_date"))
    return env.out()


_TREND_FIELDS = ("eps", "sales_per_share", "gross_margin", "operating_margin", "net_margin",
                 "fcf_margin", "roe_ttm", "debt_to_equity", "current_ratio", "growth_yoy")


def get_fundamentals(ticker: str, quarters: int = 8) -> dict[str, Any]:
    """Current valuation, growth, margins and balance sheet (TTM snapshot), the last
    quarters (default 8), and the direction of each metric over the last 4 quarters."""
    t = _tk(ticker)
    env = Envelope("get_fundamentals", t)
    if not _known(t):
        return _unknown(env.tool, t)
    snap = db.one("select * from current_fundamentals_snapshot where ticker = %s", (t,))
    if snap:
        snap.pop("id", None)
        snap.pop("ingested_at", None)
    env.put("snapshot", with_ref(snap, "current_fundamentals_snapshot", "ticker", "as_of"))
    q = db.query("select ticker, period_end, " + ", ".join(_TREND_FIELDS) + ", pe_ttm, pb, ps_ttm "
                 "from current_fundamentals where ticker = %s order by period_end desc limit %s",
                 (t, _clamp(quarters, 1, 16)))
    env.put("quarters", rows_with_ref(q, "current_fundamentals", "ticker", "period_end"))
    if q:
        env.put("trends", {
            "_ref": row_ref("calc:quarterly_trend", t, q[0]["period_end"]),
            **{f: trend_of([r.get(f) for r in q]) for f in _TREND_FIELDS},
        })
    return env.out()


def get_peer_valuation(ticker: str) -> dict[str, Any]:
    """Valuation, margins, growth and ROE next to the median of the company's
    industry peers, with the ratio company/peer for each metric."""
    t = _tk(ticker)
    env = Envelope("get_peer_valuation", t)
    if not _known(t):
        return _unknown(env.tool, t)
    row = db.one("select * from peer_comparison where ticker = %s", (t,))
    if row:
        rel = {}
        for k in ("pe_ttm", "ps_ttm", "pb", "gross_margin_ttm", "operating_margin_ttm",
                  "revenue_growth_ttm_yoy", "roe_ttm"):
            mine, peer = row.get(k), row.get(f"peer_{k}")
            rel[k] = _r(mine / peer) if mine is not None and peer not in (None, 0) else None
        row["ratio_vs_peers"] = rel
    env.put("comparison", with_ref(row, "peer_comparison", "ticker"))
    peers = db.one("select ticker, peers from company_profiles where ticker = %s", (t,))
    env.put("peers", with_ref(peers, "company_profiles", "ticker"), required=False)
    return env.out()


def get_analyst_view(ticker: str) -> dict[str, Any]:
    """What sell-side analysts think and how it is changing: rating counts and a
    1-5 score with its 3-month change, price targets and upside from the last close,
    EPS estimate revisions over 7/30/90 days per fiscal period, and recent
    upgrades/downgrades (last 90 days)."""
    t = _tk(ticker)
    env = Envelope("get_analyst_view", t)
    if not _known(t):
        return _unknown(env.tool, t)
    env.put("consensus", with_ref(db.one("select * from analyst_consensus where ticker = %s", (t,)),
                                  "analyst_consensus", "ticker", "period"))
    tgt = db.one("select ticker, snapshot_date, close_date, last_close, target_low, target_mean, "
                 "target_median, target_high, n_target_analysts, target_upside, recommendation_mean "
                 "from street_latest where ticker = %s", (t,))
    env.put("price_targets", with_ref(tgt, "street_latest", "ticker", "snapshot_date"))
    rev = db.query("select * from estimate_revisions where ticker = %s "
                   "order by array_position(array['0q','+1q','0y','+1y'], period)", (t,))
    env.put("estimate_revisions", rows_with_ref(rev, "estimate_revisions", "ticker", "period", "snapshot_date"))
    ch = db.query("select ticker, grade_date, firm, action, from_grade, to_grade, price_target_action, "
                  "prior_price_target, current_price_target from analyst_rating_changes "
                  "where ticker = %s and grade_date >= current_date - 90 order by grade_date desc", (t,))
    counts: dict[str, int] = {}
    for c in ch:
        counts[c["action"] or "?"] = counts.get(c["action"] or "?", 0) + 1
    env.put("rating_changes_90d", {"counts_by_action": counts,
                                   "latest": rows_with_ref(ch[:15], "analyst_rating_changes",
                                                           "ticker", "grade_date", "firm")},
            required=False)
    return env.out()


def get_short_interest(ticker: str) -> dict[str, Any]:
    """Short sellers' positioning: shares short, % of float, days to cover, and the
    change against the prior month (exchange data published twice a month)."""
    t = _tk(ticker)
    env = Envelope("get_short_interest", t)
    if not _known(t):
        return _unknown(env.tool, t)
    row = db.one("select ticker, snapshot_date, short_interest_date, shares_short, short_pct_float, "
                 "short_ratio, short_chg_1m, float_shares from street_latest where ticker = %s", (t,))
    env.put("short_interest", with_ref(row, "street_latest", "ticker", "snapshot_date"))
    return env.out()


def get_earnings(ticker: str) -> dict[str, Any]:
    """Next earnings report (date, before/after market, EPS and revenue estimates),
    the last reported quarter, and the last 8 EPS surprises."""
    t = _tk(ticker)
    env = Envelope("get_earnings", t)
    if not _known(t):
        return _unknown(env.tool, t)
    cols = "ticker, event_date, hour, quarter, year, eps_estimate, eps_actual, revenue_estimate, revenue_actual"
    nxt = db.one(f"select {cols} from current_earnings_calendar where ticker = %s "
                 "and event_date >= current_date order by event_date limit 1", (t,))
    if nxt:
        nxt["days_to_report"] = (date.fromisoformat(nxt["event_date"]) - date.today()).days
    env.put("next_report", with_ref(nxt, "current_earnings_calendar", "ticker", "event_date"), required=False)
    last = db.one(f"select {cols} from current_earnings_calendar where ticker = %s "
                  "and event_date < current_date order by event_date desc limit 1", (t,))
    env.put("last_report", with_ref(last, "current_earnings_calendar", "ticker", "event_date"), required=False)
    s = db.query("select ticker, period, actual, estimate, surprise, surprise_pct from current_earnings_surprises "
                 "where ticker = %s order by period desc limit 8", (t,))
    env.put("surprises", rows_with_ref(s, "current_earnings_surprises", "ticker", "period"))
    return env.out()


def get_news_sentiment(ticker: str, days: int = 90) -> dict[str, Any]:
    """News tone from FinBERT (-1 negative to +1 positive): article-weighted means
    over 1, 3, 7 and 30 days with article counts, plus the daily series. Few
    articles mean a weak signal."""
    t = _tk(ticker)
    env = Envelope("get_news_sentiment", t)
    if not _known(t):
        return _unknown(env.tool, t)
    daily = db.query("select ticker, date, n_articles, n_scored, avg_sentiment, n_positive, n_negative "
                     "from news_sentiment_daily where ticker = %s and date >= current_date - %s "
                     "order by date", (t, _clamp(days, 7, 365)))
    today = date.today()
    env.put("windows", {"_ref": row_ref("calc:weighted_sentiment", t, today.isoformat()),
                        **{f"{d}d": weighted_sentiment(daily, d, today) for d in (1, 3, 7, 30)}}
            if daily else None)
    env.put("daily", rows_with_ref(daily, "news_sentiment_daily", "ticker", "date"))
    return env.out()


def get_filing_events(ticker: str, days: int = 90, only_flags: bool = False) -> dict[str, Any]:
    """8-K and 6-K events from SEC EDGAR with their items and flags: is_results
    (2.02), is_red_flag (bankruptcy, debt acceleration, impairment, delisting notice,
    non-reliance), is_leadership_change (5.02), and the press-release sentiment."""
    t = _tk(ticker)
    env = Envelope("get_filing_events", t)
    if not _known(t):
        return _unknown(env.tool, t)
    flt = " and (is_results or is_red_flag or is_leadership_change)" if only_flags else ""
    rows = db.query("select id, ticker, form_type, filed_at, url, items, sentiment, is_results, is_red_flag, "
                    f"is_leadership_change from filing_events where ticker = %s{flt} "
                    "and filed_at >= now() - make_interval(days => %s) order by filed_at desc limit 30",
                    (t, _clamp(days, 1, 730)))
    env.put("events", rows_with_ref(rows, "filing_events", "id"), required=False)
    if any(r.get("is_red_flag") for r in rows):
        env.note("Hay al menos un 8-K de alerta en la ventana.")
    return env.out()


def get_insider_activity(ticker: str, days: int = 90) -> dict[str, Any]:
    """Insider buying and selling: the 30-day summary (open-market buys vs
    discretionary sells vs 10b5-1 plan sells), recent Form 4 trades with role, and
    Finnhub's monthly MSPR (-100 to +100) for the last 12 months. Plan sales say
    little; buys and discretionary sells are the signal."""
    t = _tk(ticker)
    env = Envelope("get_insider_activity", t)
    if not _known(t):
        return _unknown(env.tool, t)
    env.put("summary_30d", with_ref(db.one("select * from insider_activity_30d where ticker = %s", (t,)),
                                    "insider_activity_30d", "ticker"), required=False)
    trades = db.query("select id, ticker, filed_at, owner, role, is_10b5_1, codes, buy_value, sell_value, "
                      "net_shares from insider_trades where ticker = %s "
                      "and filed_at >= now() - make_interval(days => %s) order by filed_at desc limit 25",
                      (t, _clamp(days, 1, 365)))
    env.put("trades", rows_with_ref(trades, "insider_trades", "id"), required=False)
    mspr = db.query("select distinct on (year, month) ticker, year, month, mspr, change "
                    "from raw_insider_sentiment where ticker = %s "
                    "order by year desc, month desc, valid_from desc limit 12", (t,))
    env.put("mspr_monthly", rows_with_ref(mspr, "raw_insider_sentiment", "ticker", "year", "month"))
    return env.out()


def get_catalysts(ticker: str) -> dict[str, Any]:
    """What is coming and what just happened: days to earnings with estimates,
    recent surprises and beats, counts of 8-K / Form 4 / Form 144 in 30 days,
    dividends and splits."""
    t = _tk(ticker)
    env = Envelope("get_catalysts", t)
    if not _known(t):
        return _unknown(env.tool, t)
    env.put("catalysts", with_ref(db.one("select * from catalysts where ticker = %s", (t,)), "catalysts", "ticker"))
    return env.out()


def get_macro(series: list[str] | None = None, history_days: int = 0) -> dict[str, Any]:
    """US macro context from FRED with changes over 1, 3 and 12 months: fed funds,
    3-month/2-year/10-year yields, 10y-2y curve, CPI, unemployment, jobless claims,
    VIX, high-yield spread, WTI oil and USD/MXN (DEXMXUS). Optionally the daily
    history of chosen series."""
    env = Envelope("get_macro")
    sel = [s.upper() for s in series] if series else None
    sql = "select * from macro_latest" + (" where series_id = any(%s)" if sel else "") + " order by series_id"
    latest = db.query(sql, (sel,) if sel else ())
    env.put("latest", rows_with_ref(latest, "macro_latest", "series_id", "obs_date"))
    if history_days and sel:
        hist = db.query("select series_id, obs_date, value from macro_observations where series_id = any(%s) "
                        "and obs_date >= current_date - %s order by series_id, obs_date",
                        (sel, _clamp(history_days, 1, 730)))
        env.put("history", rows_with_ref(hist, "macro_observations", "series_id", "obs_date"), required=False)
    return env.out()


_SOURCE_JOBS = ("ingest_prices", "compute_indicators", "ingest_news", "ingest_filings", "enrich_filings",
                "ingest_fundamentals", "ingest_analysts", "ingest_street", "ingest_earnings",
                "ingest_earncal", "ingest_insider_sentiment", "ingest_macro", "ingest_profiles")


def get_data_status(ticker: str | None = None) -> dict[str, Any]:
    """Can the data be trusted right now: per-ticker quality flags (stale prices,
    bad bars, unexplained jumps, stale fundamentals) and the last run of each source
    pipeline with its status."""
    env = Envelope("get_data_status", ticker)
    if ticker:
        t = _tk(ticker)
        flags = db.query("select ticker, check_name, severity, detail from data_quality_flags "
                         "where ticker = %s", (t,))
        env.put("quality_flags", rows_with_ref(flags, "data_quality_flags", "ticker", "check_name"), required=False)
    runs = db.query("select distinct on (job_name) run_id, job_name, status, started_at, ended_at, rows_written "
                    "from job_runs where job_name = any(%s) order by job_name, started_at desc",
                    (list(_SOURCE_JOBS),))
    env.put("last_runs", rows_with_ref(runs, "job_runs", "run_id"))
    bad = [r["job_name"] for r in runs if r["status"] in ("failed", "abandoned")]
    if bad:
        env.note("Fuentes con la última corrida fallida: " + ", ".join(bad))
    return env.out()


def get_cost_model(amount_mxn: float | None = None, market: str = "SIC") -> dict[str, Any]:
    """GBM trading costs for a round trip (buy + sell) as a fraction of the amount:
    commission with IVA, FX spread (SIC only) and slippage, and the minimum
    expected move worth a buy (3x the round trip). Includes the current USD/MXN."""
    env = Envelope("get_cost_model")
    text = (_ROOT / "config/cost_model.yaml").read_text()
    costs = round_trip_cost(yaml.safe_load(text), amount_mxn, sic=market.upper() == "SIC")
    env.put("costs", {"_ref": row_ref("config", "cost_model.yaml"), "market": market.upper(),
                      "amount_mxn": amount_mxn, **costs})
    if market.upper() == "SIC" and "PLACEHOLDER" in text:
        env.note("El spread de FX es un estimado; falta verificarlo en un estado de cuenta de GBM.")
    fx = db.one("select series_id, obs_date, value from macro_latest where series_id = 'DEXMXUS'")
    env.put("usd_mxn", with_ref(fx, "macro_latest", "series_id", "obs_date"), required=False)
    return env.out()


def get_research_digest(ticker: str) -> dict[str, Any]:
    """Chair only: the whole ticker in one compact answer (overview, technicals,
    fundamentals, peers, analysts, short interest, earnings, news tone, filings,
    insiders, catalysts and data status). Members must use their own tools."""
    t = _tk(ticker)
    if not _known(t):
        return _unknown("get_research_digest", t)
    env = Envelope("get_research_digest", t)
    parts = {
        "overview": get_ticker_overview(t), "technicals": get_price_technicals(t),
        "fundamentals": get_fundamentals(t, quarters=4), "peers": get_peer_valuation(t),
        "analysts": get_analyst_view(t), "short_interest": get_short_interest(t),
        "earnings": get_earnings(t), "news_sentiment": get_news_sentiment(t, days=30),
        "filings": get_filing_events(t, days=60, only_flags=True), "insiders": get_insider_activity(t, days=60),
        "catalysts": get_catalysts(t),
    }
    for name, res in parts.items():
        data = dict(res.get("data") or {})
        data.pop("daily", None)  # the chair gets the windows, not the series
        env.put(name, data)
        env.missing += [f"{name}.{m}" for m in (res.get("data_status") or {}).get("missing", [])]
        env.notes += res.get("notes", [])
    return env.out()


ALL_TOOLS = {f.__name__: f for f in (
    get_ticker_overview, get_price_technicals, get_fundamentals, get_peer_valuation, get_analyst_view,
    get_short_interest, get_earnings, get_news_sentiment, get_filing_events, get_insider_activity,
    get_catalysts, get_macro, get_data_status, get_cost_model, get_research_digest,
)}
