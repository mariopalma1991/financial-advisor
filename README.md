# financial-advisor

Data engineering platform for a multi-agent financial advisor (swing trading, days-to-month horizon).
Architecture and decisions: [`plans/data-engineering-platform-plan.md`](plans/data-engineering-platform-plan.md) ·
Ticker universe: [`plans/ticker-universe.md`](plans/ticker-universe.md)

## What it does (Phase 1)

- Ingests **daily prices** (yfinance primary, Stooq cross-check) and **fundamentals** (SEC EDGAR XBRL) for a two-tier universe: ~115 screening instruments + a small analysis tier.
- Computes the **swing indicator set** (SMA/EMA, RSI, MACD, ATR, Bollinger, relative strength) on point-in-time-adjusted prices.
- Runs a configurable **screener** (`config/screens.yaml`) that surfaces candidates from the screening tier.
- Builds versioned, role-scoped **feeds** per analysis-tier ticker (shared `snapshot_id`, `as_of` capable).
- Serves everything through an **MCP server** (stdio) — queryable from Claude Desktop or any MCP client.
- Emits structured **pipeline events** to Supabase for observability (queryable via MCP tools).

## Setup

```bash
# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # macOS / Linux

# Install the project and its dependencies
pip install -e ".[dev]"

# Configure credentials
copy .env.example .env   # then fill in Supabase credentials
python scripts/apply_migration.py
```

`.env` needs: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL` (direct Postgres string), optional `ALERT_WEBHOOK_URL`.

## Daily operation (local)

All commands assume the venv is activated. From the project root:

```bash
python scripts/dev_cycle.py AAPL MSFT        # full cycle for a few tickers
```

Or individual jobs:

```bash
python -m src.jobs.ingest_prices             # prices + cross-check + freshness
python -m src.jobs.compute_indicators        # add --full to rewrite history
python -m src.jobs.ingest_filings            # EDGAR fundamentals + filings
python -m src.jobs.ingest_news               # RSS news -> ticker linking -> FinBERT sentiment
python -m src.jobs.run_screener              # screens -> screener_results
python -m src.jobs.build_feeds               # feeds -> research_packages
python -m src.jobs.backfill AAPL WALMEX.MX   # history for new/promoted tickers
python scripts/promote_ticker.py NVDA        # screening -> analysis tier
```

## Dashboard

```bash
streamlit run src/dashboard/app.py
```

Four pages: **Pipeline** (run history, events, freshness), **Market Data** (OHLCV charts, indicators, fundamentals), **News** (articles, sentiment trends, volume), **Screener** (candidates, screen history).

## MCP server

```bash
python -m src.mcp_server.server              # stdio transport
```

Tools: `get_research_package`, `get_feed`, `get_price_history`, `get_fundamentals`,
`search_news`, `get_news_sentiment`, `run_screener`, `get_screener_results`,
`list_tickers`, `get_pipeline_runs`, `get_pipeline_events`.

## Cloud operation

GitHub Actions runs the pipeline on schedule (see `.github/workflows/`):
`daily_pipeline.yml` (prices → indicators → screener → feeds, 01:00 UTC Tue–Sat),
`ingest_filings.yml` (daily 12:00 UTC), and `ingest_news.yml` (3×/day at 13, 19, 23 UTC).
Required repo secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL`,
optional `ALERT_WEBHOOK_URL`; optional repo variable: `EDGAR_USER_AGENT`.

## Tests

```bash
python -m pytest tests -q
```
