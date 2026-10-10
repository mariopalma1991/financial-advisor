"""Pipeline view — run history, event timeline, data freshness."""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from src.storage import curated


@st.cache_data(ttl=120)
def _pipeline_runs():
    return curated.get_pipeline_runs(limit=50)


@st.cache_data(ttl=120)
def _pipeline_events(limit: int = 200):
    return curated.get_pipeline_events(limit=limit)


@st.cache_data(ttl=300)
def _tickers():
    return curated.enabled_tickers()


@st.cache_data(ttl=300)
def _latest_bars():
    return curated.get_latest_bars_all()


def render():
    st.header("📊 Pipeline Health")

    runs = _pipeline_runs()
    if not runs:
        st.info("No pipeline runs recorded yet.")
        return

    # ---- Summary metrics ----
    cols = st.columns(4)
    total = len(runs)
    errors = sum(1 for r in runs if r.get("errors", 0) > 0)
    latest = runs[0]
    cols[0].metric("Total Runs", total)
    cols[1].metric("Failed", errors, delta=None if errors == 0 else f"-{errors}")
    cols[2].metric("Last Run", str(latest.get("finished_at", ""))[:19].replace("T", " "))
    cols[3].metric("Last Job", latest.get("job_name", "—"))

    # ---- Run history table ----
    st.subheader("Run History")
    df = pd.DataFrame(runs)
    if not df.empty:
        df["started"] = pd.to_datetime(df["started_at"]).dt.strftime("%m-%d %H:%M")
        df["duration"] = (
            pd.to_datetime(df["finished_at"]) - pd.to_datetime(df["started_at"])
        ).dt.total_seconds().round(0).astype(int)
        df["status"] = df["errors"].apply(lambda e: "❌" if e > 0 else "✅")
        st.dataframe(
            df[["status", "job_name", "started", "duration", "events", "errors"]].head(20),
            width='stretch',
            hide_index=True,
        )

    # ---- Recent events ----
    st.subheader("Recent Events")
    events = _pipeline_events(limit=100)
    if events:
        ev_df = pd.DataFrame(events)
        ev_df["time"] = pd.to_datetime(ev_df["created_at"]).dt.strftime("%m-%d %H:%M:%S")
        ev_df["lvl"] = ev_df["level"].map({"ERROR": "🔴", "WARN": "🟡", "INFO": "🔵"})
        st.dataframe(
            ev_df[["lvl", "time", "job_name", "event_type", "message", "ticker"]].head(30),
            width='stretch',
            hide_index=True,
        )

    # ---- Data freshness ----
    st.subheader("Data Freshness")
    tickers = _tickers()
    bars = _latest_bars()
    today = date.today()

    fresh_rows = []
    for t in tickers[:30]:  # top 30 by tier
        sym = t["ticker"]
        bar = bars.get(sym)
        if bar:
            bar_date = bar.get("bar_date")
            if isinstance(bar_date, str):
                bar_date = date.fromisoformat(bar_date)
            age = (today - bar_date).days if bar_date else None
            fresh_rows.append({
                "ticker": sym,
                "tier": t.get("tier", "—"),
                "last_bar": str(bar_date) if bar_date else "—",
                "age_days": age,
                "fresh": "✅" if age is not None and age <= 2 else "⚠️" if age is not None and age <= 5 else "🔴",
            })

    if fresh_rows:
        st.dataframe(
            pd.DataFrame(fresh_rows),
            width='stretch',
            hide_index=True,
        )