"""Screener view — latest candidates, screen history, metric distributions."""

import pandas as pd
import plotly.express as px
import streamlit as st

from src.storage import curated


@st.cache_data(ttl=300)
def _screener_results():
    return curated.get_screener_results(matched_only=False)


def render():
    st.header("🎯 Screener")

    results = _screener_results()
    if not results:
        st.info("No screener results yet.")
        return

    df = pd.DataFrame(results)

    # ---- Latest candidates ----
    st.subheader("Latest Candidates")
    latest_date = df["result_date"].max()
    latest = df[df["result_date"] == latest_date]
    matched = latest[latest["matched"] == True]  # noqa: E712

    cols = st.columns(3)
    cols[0].metric("Latest Run", str(latest_date))
    cols[1].metric("Candidates", len(matched))
    cols[2].metric("Screens Run", latest["screen_name"].nunique())

    if not matched.empty:
        st.dataframe(
            matched[["ticker", "screen_name", "metrics_json"]].head(20),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No candidates matched in the latest run.")

    # ---- Screen performance over time ----
    st.subheader("Screen Match History")
    screens = df["screen_name"].unique()
    selected_screen = st.selectbox("Screen", screens)

    screen_df = df[df["screen_name"] == selected_screen].copy()
    screen_df["result_date"] = pd.to_datetime(screen_df["result_date"])
    daily_matches = (
        screen_df[screen_df["matched"] == True]  # noqa: E712
        .groupby("result_date")
        .size()
        .reset_index(name="matches")
    )

    if not daily_matches.empty:
        fig = px.bar(
            daily_matches,
            x="result_date",
            y="matches",
            template="plotly_dark",
            title=f"Daily matches — {selected_screen}",
        )
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # ---- All results table ----
    st.subheader("All Results")
    display_df = df.copy()
    display_df["result_date"] = pd.to_datetime(display_df["result_date"]).dt.strftime("%Y-%m-%d")
    display_df["matched_icon"] = display_df["matched"].map({True: "✅", False: "—"})
    st.dataframe(
        display_df[["result_date", "screen_name", "ticker", "matched_icon"]].head(50),
        use_container_width=True,
        hide_index=True,
    )