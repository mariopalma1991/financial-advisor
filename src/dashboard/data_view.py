"""Market Data view — price charts, indicators, fundamentals browser."""

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.storage import curated


@st.cache_data(ttl=600)
def _tickers():
    return curated.enabled_tickers()


@st.cache_data(ttl=300)
def _price_history(ticker: str, days: int = 120):
    return curated.get_price_history(ticker, window_days=days)


@st.cache_data(ttl=300)
def _indicators(ticker: str, days: int = 120):
    return curated.get_indicators(ticker, window_days=days)


@st.cache_data(ttl=600)
def _fundamentals(ticker: str):
    return curated.get_fundamentals(ticker, quarters=8)


def render():
    st.header("📈 Market Data")

    tickers = _tickers()
    symbols = [t["ticker"] for t in tickers]
    selected = st.selectbox("Ticker", symbols, index=0 if symbols else None)

    if not selected:
        st.info("No tickers available.")
        return

    info = next((t for t in tickers if t["ticker"] == selected), {})
    st.caption(f"{info.get('name', '')} · {info.get('sector', '')} · {info.get('tier', '')} tier")

    # ---- Price chart ----
    st.subheader("Price & Indicators")
    days = st.slider("Lookback (trading days)", 30, 252, 120)
    bars = _price_history(selected, days)
    inds = _indicators(selected, days)

    if not bars:
        st.warning("No price data.")
        return

    df = pd.DataFrame(bars)
    df["bar_date"] = pd.to_datetime(df["bar_date"])

    fig = go.Figure()

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df["bar_date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="OHLC",
        )
    )

    # Overlay SMAs if indicator data exists
    if inds:
        idf = pd.DataFrame(inds)
        idf["bar_date"] = pd.to_datetime(idf["bar_date"])
        for ma, color in [("sma_20", "blue"), ("sma_50", "orange"), ("sma_200", "red")]:
            if ma in idf.columns:
                fig.add_trace(
                    go.Scatter(
                        x=idf["bar_date"],
                        y=idf[ma],
                        mode="lines",
                        name=ma.upper(),
                        line=dict(width=1, color=color),
                    )
                )

    fig.update_layout(
        height=500,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
    )
    st.plotly_chart(fig, use_container_width=True)

    # ---- Indicator summary ----
    if inds:
        st.subheader("Indicator Summary")
        latest = inds[-1]
        cols = st.columns(5)
        cols[0].metric("RSI(14)", _fmt(latest.get("rsi_14")))
        cols[1].metric("MACD", _fmt(latest.get("macd")))
        cols[2].metric("ATR(14)", _fmt(latest.get("atr_14")))
        cols[3].metric("SMA 50", _fmt(latest.get("sma_50")))
        cols[4].metric("SMA 200", _fmt(latest.get("sma_200")))

    # ---- Fundamentals ----
    st.subheader("Fundamentals")
    funds = _fundamentals(selected)
    if funds:
        fdf = pd.DataFrame(funds)
        cols_to_show = [c for c in ["period_end", "revenue", "net_income", "eps", "gross_margin", "net_margin", "growth_yoy"] if c in fdf.columns]
        st.dataframe(fdf[cols_to_show], use_container_width=True, hide_index=True)
    else:
        st.info("No fundamentals data.")


def _fmt(v):
    if v is None:
        return "—"
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return str(v)[:8]