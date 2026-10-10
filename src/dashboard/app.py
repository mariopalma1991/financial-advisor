"""Streamlit dashboard — pipeline health, market data, news, screener.

Run: streamlit run src/dashboard/app.py
"""

import streamlit as st

from src.dashboard import data_view, news_view, pipeline_view, screener_view

st.set_page_config(
    page_title="Financial Advisor — Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Financial Advisor — Data Platform")

st.markdown("Pipeline health, market data, news sentiment, and screener results.")

view = st.sidebar.radio(
    "Navigate",
    ["📊 Pipeline", "📈 Market Data", "📰 News", "🎯 Screener"],
)

if view == "📊 Pipeline":
    pipeline_view.render()
elif view == "📈 Market Data":
    data_view.render()
elif view == "📰 News":
    news_view.render()
elif view == "🎯 Screener":
    screener_view.render()