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
python -m src.mcp_server.server --role optimista   # stdio transport; needs SUPABASE_DB_URL
```

Read-only tools over the curated views: `get_ticker_overview`, `get_price_technicals`,
`get_fundamentals`, `get_peer_valuation`, `get_analyst_view`, `get_short_interest`,
`get_earnings`, `get_news_sentiment`, `get_filing_events`, `get_insider_activity`,
`get_catalysts`, `get_price_series`, `get_news`, `get_filing_text`, `get_risk_factors`,
`get_macro`, `get_data_status`, `get_cost_model`, `get_research_digest`, `screen_universe`,
`get_factor_scores`, `get_portfolio`, `get_alert_history`.
Long texts (news summaries, 8-K press releases, 10-K risk factors) come from the
Parquet archive, so the server also needs the `SUPABASE_S3_*` settings; risk factors
are read from EDGAR the first time a 10-K is asked for and cached there.
`get_short_interest` adds FINRA's twice-monthly history (`ingest_finra_short`, weekly).
Every answer carries a `snapshot_id` and a `_ref` per row that agents cite as
`<_ref>#<field>`. `config/mcp_roles.yaml` sets which tools each committee role sees.

Every tool takes `as_of` (`YYYY-MM-DD` = end of that day, or an ISO timestamp) and
returns only what was known then, so a committee can be replayed on a past date
(SQL functions `*_at` in `db/migrations/0015_point_in_time.sql`). `resolve_refs`
(role `validador`, never an agent) looks each cited number up again at the same
`as_of` and flags mismatches, with no LLM involved.
`mcp_smoke` (manual workflow) checks every tool live and on a past date, and that
every number in the answers resolves back through `resolve_refs`.

## Factor scores

`compute_factors` (after every `ingest_prices` run) scores each tracked stock as of the
day's close on five factors, each a z-score within its industry: 12-1 momentum,
revisions (EPS estimate moves, plus analysts' 90-day price-target changes and
up/downgrades from `analyst_rating_changes`, kept 2 years), post-earnings drift,
insider buying (fixed scale; routine board buys excluded) and quality. Insider buys
before our own Form 4 loads come from SEC's quarterly Insider Transactions Data Sets
(`backfill_insider_purchases`, manual workflow, table `insider_purchases`). `score` is their mean (a missing factor counts as 0) and
`rank` orders stocks that trade at least 1M USD a day. Inputs are read point in time,
so past month-ends can be rebuilt (`--backfill-months`). Table `factor_scores` keeps
40 daily days plus every month-end; view `factor_latest`.

## Model portfolio and committee log

Amounts the investor sees are in Mexican pesos (USD/MXN from FRED `DEXMXUS`).

- `paper_portfolio` (after `compute_factors`, settings in `config/paper_portfolio.yaml`)
  runs the model portfolio: the best factor-ranked stocks, at most 25% per industry,
  held while ranked within 40, rebalanced at month-ends at the next day's close, with
  GBM costs, dividend withholding and 10% ISR. Whole shares only: the investor's budget
  (an initial deposit plus later ones in `investor_contributions`) sets how many
  positions fit (`min_position_mxn`), and a stock whose one share costs more than a
  position is skipped until the budget grows. VOO gets the same pesos on the same days.
  `forward_top20` stores each decision once (from `forward_start`); `backtest_top20`
  replays the past year, and `backtest_top20_<amount>` replays it for each of
  `backtest_amounts` (the dashboard's "other amount" view). The investor sets the
  initial amount and adds deposits on the "Mi inversión" page; a check every 10 minutes
  reruns the job when `investor_contributions` changed. Every book also simulates a
  recurring deposit (`recurring_mxn` on `recurring_days`, e.g. 500 MXN on the 15th and
  the month's last day). Tables `paper_decisions`, `paper_fills`, `paper_nav`; view
  `paper_performance`; MCP `get_portfolio`.
- `committee_runs`, `committee_opinions`, `committee_alerts`: written by the committee
  runner: each run's as_of (replayable), each agent's stance, reasoning, cited refs and
  tool calls, and the alerts that reach the investor (vetoed buys kept, to score the veto).
- `score_alerts` (after `compute_factors`) fills `alert_outcomes`: each alert against
  VOO in MXN after 21, 63 and 126 trading days, from the next day's close. Views
  `alert_scorecard` and `agent_scorecard`; MCP `get_alert_history` shows past calls.

## Agent committee (Capa 3)

`src/jobs/run_committee.py` (workflow `committee`; needs `ANTHROPIC_API_KEY`)
reviews candidates from the factor list and approves or vetoes each buy: the committee
is a veto over the list, it does not pick stocks. Settings in `config/committee.yaml`,
prompts in `src/committee/prompts.py`.

- Five members (optimista, pesimista, contrario, especialista, riesgo_timing) analyze
  the ticker in separate conversations, each with only its MCP tools and call budget
  (`config/mcp_roles.yaml`), all at the same `as_of` (forced by the runner, so a run
  replays). Each ends with `entregar_opinion`: stance, 1-10 score, confidence, thesis
  and every number cited as `<_ref>#<field>`.
- `resolve_refs` checks the members' citations; the chair (presidente) sees their
  opinions with the cites that did not verify, and ends with `entregar_decision`
  (approve or veto, target and stop as % of the MXN price, invalidation criteria).
- Each run writes `committee_runs`, one `committee_opinions` row per agent, and one
  `committee_alerts` buy (vetoed or not), so `score_alerts` grades the chair and every
  member against VOO. The full trace goes to Storage under `committee_traces/` and to the
  workflow's artifacts. A run stops once it spends `max_usd_per_run`.

```bash
python -m src.jobs.run_committee AAPL            # one ticker, latest data
python -m src.jobs.run_committee --top 20        # the day's top 20 by factor rank
python -m src.jobs.run_committee AAPL --as-of 2026-10-16
python -m src.jobs.run_committee --new-buys      # the model portfolio's latest buys not yet reviewed
```

On schedule (22:15 and 06:15 CST) the workflow runs `--new-buys`: only the stocks
`forward_top20` decided to buy on its latest decision day, before they fill at the next
close, at most `max_new_buys` per run; a ticker that failed twice is left for a person.

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
