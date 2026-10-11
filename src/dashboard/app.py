"""Streamlit dashboard — unified ticker view + pipeline health + screener.

Run: streamlit run src/dashboard/app.py
"""

import streamlit as st

from src.dashboard import pipeline_view, screener_view, ticker_view

st.set_page_config(
    page_title="Financial Advisor — Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("📊 Financial Advisor — Data Platform")

view = st.sidebar.radio(
    "Navigate",
    ["🔍 Ticker Overview", "📊 Pipeline", "🎯 Screener"],
)

if view == "🔍 Ticker Overview":
    ticker_view.render()
elif view == "📊 Pipeline":
    pipeline_view.render()
elif view == "🎯 Screener":
    screener_view.render()