"""News view — recent articles, sentiment trends, ticker volume."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.storage import curated


@st.cache_data(ttl=120)
def _tickers():
    return curated.enabled_tickers()


@st.cache_data(ttl=120)
def _news_articles(ticker: str | None = None, days: int = 7, limit: int = 200):
    return curated.get_news_articles(ticker=ticker, window_days=days, limit=limit)


@st.cache_data(ttl=120)
def _news_sentiment(ticker: str, days: int = 30):
    return curated.get_news_sentiment(ticker, window_days=days)


def render():
    st.header("📰 News & Sentiment")

    tickers = _tickers()
    symbols = ["(all)"] + [t["ticker"] for t in tickers]
    selected = st.selectbox("Ticker", symbols, index=0)

    ticker = None if selected == "(all)" else selected
    days = st.slider("Lookback (days)", 1, 30, 7)

    articles = _news_articles(ticker=ticker, days=days)

    if not articles:
        st.info("No news articles found.")
        return

    # ---- Sentiment trend (only when a specific ticker is selected) ----
    if ticker:
        st.subheader(f"Sentiment Trend — {ticker}")
        sent = _news_sentiment(ticker, days=30)
        if sent:
            sdf = pd.DataFrame(sent)
            sdf["date"] = pd.to_datetime(sdf["date"])
            fig = go.Figure()
            for col, color in [("sent_1d", "blue"), ("sent_3d", "orange"), ("sent_7d", "green")]:
                if col in sdf.columns:
                    fig.add_trace(
                        go.Scatter(
                            x=sdf["date"],
                            y=sdf[col],
                            mode="lines+markers",
                            name=col,
                            line=dict(color=color),
                        )
                    )
            fig.update_layout(
                height=350,
                margin=dict(l=0, r=0, t=0, b=0),
                template="plotly_dark",
                yaxis_title="Sentiment",
                yaxis_range=[-1, 1],
            )
            st.plotly_chart(fig, use_container_width=True)

    # ---- Top tickers by volume ----
    if ticker is None:
        st.subheader("News Volume by Ticker")
        ticker_counts: dict[str, int] = {}
        for a in articles:
            for t in a.get("tickers", []):
                ticker_counts[t] = ticker_counts.get(t, 0) + 1
        top = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)[:15]
        if top:
            tdf = pd.DataFrame(top, columns=["ticker", "articles"])
            fig = px.bar(tdf, x="ticker", y="articles", template="plotly_dark")
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, use_container_width=True)

    # ---- Articles table ----
    st.subheader(f"Articles ({len(articles)})")
    adf = pd.DataFrame(articles)
    adf["date"] = pd.to_datetime(adf["published_at"]).dt.strftime("%m-%d %H:%M")
    adf["sent"] = adf["sentiment_score"].apply(
        lambda s: f"{s:+.2f}" if s is not None else "—"
    )
    adf["tickers_str"] = adf["tickers"].apply(
        lambda ts: ", ".join(ts) if ts else ""
    )
    cols = [c for c in ["date", "tickers_str", "headline", "sent"] if c in adf.columns]
    st.dataframe(
        adf[cols].head(50),
        use_container_width=True,
        hide_index=True,
        column_config={
            "date": "Date",
            "tickers_str": "Tickers",
            "headline": "Headline",
            "sent": "Sentiment",
        },
    )