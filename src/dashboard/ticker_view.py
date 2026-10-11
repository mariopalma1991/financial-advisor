"""Unified Ticker View — market data, news, sentiment, fundamentals in one place."""
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


@st.cache_data(ttl=120)
def _news_articles(ticker: str, days: int = 7):
    return curated.get_news_articles(ticker=ticker, window_days=days, limit=50)


@st.cache_data(ttl=120)
def _news_sentiment(ticker: str, days: int = 30):
    return curated.get_news_sentiment(ticker, window_days=days)


def _price_chart(bars: list[dict], inds: list[dict], days: int) -> None:
    """Candlestick chart with SMA overlays."""
    df = pd.DataFrame(bars)
    df["bar_date"] = pd.to_datetime(df["bar_date"])

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["bar_date"], open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="OHLC",
    ))

    if inds:
        idf = pd.DataFrame(inds)
        idf["bar_date"] = pd.to_datetime(idf["bar_date"])
        for ma, color in [("sma_20", "blue"), ("sma_50", "orange"), ("sma_200", "red")]:
            if ma in idf.columns:
                fig.add_trace(go.Scatter(
                    x=idf["bar_date"], y=idf[ma], mode="lines",
                    name=ma.upper(), line=dict(width=1, color=color),
                ))

    fig.update_layout(
        height=450, margin=dict(l=0, r=0, t=0, b=0),
        template="plotly_dark", xaxis_title="", yaxis_title="Price",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(rangeslider_visible=False)
    st.plotly_chart(fig, width="stretch")


def _indicator_gauges(inds: list[dict], bars: list[dict]) -> None:
    """Key metrics row: latest price, RSI, MACD, volume."""
    if not inds or not bars:
        return

    latest_ind = inds[-1]
    latest_bar = bars[-1]

    cols = st.columns(6)
    cols[0].metric("Close", f"${latest_bar.get('close', 0):.2f}")
    cols[1].metric("RSI(14)", f"{latest_ind.get('rsi_14', 0):.1f}")
    cols[2].metric("MACD", f"{latest_ind.get('macd', 0):.3f}")
    cols[3].metric("ATR(14)", f"{latest_ind.get('atr_14', 0):.2f}")
    vol = latest_ind.get('volume_ma_20') or 0
    cols[4].metric("Vol MA20", f"{vol:,.0f}")
    rs = latest_ind.get('rel_strength_21d') or 0
    cols[5].metric("Rel Str 21d", f"{rs:.2%}")


def _fundamentals_table(funds: list[dict]) -> None:
    """Quarterly fundamentals table."""
    if not funds:
        st.info("No fundamentals available.")
        return

    df = pd.DataFrame(funds)
    df["period_end"] = pd.to_datetime(df["period_end"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values("period_end", ascending=False)

    display = df[[
        "period_end", "revenue", "net_income", "eps",
        "gross_margin", "net_margin", "total_debt", "equity",
    ]].copy()

    # Format
    for col in ["revenue", "net_income", "total_debt", "equity"]:
        if col in display.columns:
            display[col] = display[col].apply(
                lambda x: f"${x/1e9:.2f}B" if pd.notna(x) else "—"
            )
    for col in ["gross_margin", "net_margin"]:
        if col in display.columns:
            display[col] = display[col].apply(
                lambda x: f"{x:.1%}" if pd.notna(x) else "—"
            )
    if "eps" in display.columns:
        display["eps"] = display["eps"].apply(
            lambda x: f"${x:.2f}" if pd.notna(x) else "—"
        )

    display.columns = [
        "Quarter", "Revenue", "Net Income", "EPS",
        "Gross Margin", "Net Margin", "Total Debt", "Equity",
    ]
    st.dataframe(display, width="stretch", hide_index=True)


def _news_section(ticker: str) -> None:
    """News articles + sentiment chart for a ticker."""
    articles = _news_articles(ticker, days=7)
    sent = _news_sentiment(ticker, days=30)

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("📰 Recent News")
        if not articles:
            st.info("No news articles found.")
        else:
            for a in articles[:10]:
                headline = a.get("headline", "No headline")
                pub = a.get("published_at", "")[:10]
                score = a.get("sentiment_score")
                emoji = "🟢" if (score or 0) > 0.1 else ("🔴" if (score or 0) < -0.1 else "⚪")
                st.markdown(f"{emoji} **{headline}**  \n*{pub}* — score: {score:.2f}" if score else f"⚪ **{headline}**  \n*{pub}*")

    with col_right:
        st.subheader("📈 Sentiment Trend")
        if sent:
            sdf = pd.DataFrame(sent)
            sdf["date"] = pd.to_datetime(sdf["date"])
            fig = go.Figure()
            for col, color in [("sent_1d", "blue"), ("sent_3d", "orange"), ("sent_7d", "green")]:
                if col in sdf.columns:
                    fig.add_trace(go.Scatter(
                        x=sdf["date"], y=sdf[col], mode="lines+markers",
                        name=col, line=dict(color=color),
                    ))
            fig.update_layout(
                height=350, margin=dict(l=0, r=0, t=0, b=0),
                template="plotly_dark", yaxis_title="Sentiment", yaxis_range=[-1, 1],
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No sentiment data.")


def render() -> None:
    st.header("🔍 Ticker Overview")

    tickers = _tickers()
    symbols = [t["ticker"] for t in tickers]

    if not symbols:
        st.warning("No tickers available. Run backfill first.")
        return

    selected = st.selectbox("Select Ticker", symbols, index=0)

    info = next((t for t in tickers if t["ticker"] == selected), {})
    st.caption(
        f"**{info.get('name', selected)}** · {info.get('exchange', '')} · "
        f"{info.get('sector', '')} · {info.get('currency', 'USD')}"
    )

    # ── Tab layout ──
    tab_price, tab_fund, tab_news = st.tabs(["📈 Price & Indicators", "💰 Fundamentals", "📰 News & Sentiment"])

    with tab_price:
        days = st.slider("Lookback (trading days)", 30, 1260, 120, key="price_days")
        bars = _price_history(selected, days)
        inds = _indicators(selected, days)

        if not bars:
            st.warning("No price data.")
        else:
            _indicator_gauges(inds, bars)
            st.markdown("")  # spacer
            _price_chart(bars, inds, days)

    with tab_fund:
        st.subheader(f"Quarterly Fundamentals — {selected}")
        funds = _fundamentals(selected)
        _fundamentals_table(funds)

    with tab_news:
        _news_section(selected)