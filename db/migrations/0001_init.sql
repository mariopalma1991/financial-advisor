-- 0001_init.sql — initial schema for the financial-advisor data platform.
-- Apply in Supabase (SQL editor or `supabase db push`).
-- Conventions: every table carries valid_from / ingested_at (bitemporal) and is insert-only;
-- "current" state = latest ingested_at row per natural key (convenience views at the bottom).

-- ============ RAW ZONE ============

create table if not exists raw_price_bars (
    id            bigint generated always as identity primary key,
    source        text not null,              -- yfinance | stooq
    ticker        text not null,
    bar_date      date not null,
    open          double precision,
    high          double precision,
    low           double precision,
    close         double precision,
    volume        bigint,
    payload_json  jsonb,                      -- untouched source payload
    valid_from    timestamptz not null,       -- when the bar became true (bar close)
    ingested_at   timestamptz not null default now(),
    unique (source, ticker, bar_date)         -- idempotent re-runs (upsert-ignore)
);
create index if not exists idx_raw_price_bars_lookup
    on raw_price_bars (ticker, bar_date desc, source);

create table if not exists raw_corporate_actions (
    id            bigint generated always as identity primary key,
    source        text not null,
    ticker        text not null,
    action_type   text not null,              -- dividend | split
    ex_date       date not null,
    amount        double precision,           -- dividend amount per share
    ratio         double precision,           -- split ratio (e.g. 4 for 4:1)
    payload_json  jsonb,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now(),
    unique (source, ticker, ex_date, action_type)   -- idempotent re-runs
);
create index if not exists idx_raw_corp_actions_lookup
    on raw_corporate_actions (ticker, ex_date desc);

create table if not exists raw_news_articles (    -- Phase 2
    id            bigint generated always as identity primary key,
    source        text not null,
    article_id    text not null,              -- stable hash of url
    published_at  timestamptz not null,
    url           text,
    headline      text,
    storage_path  text,                       -- body lives in Supabase Storage
    payload_json  jsonb,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now(),
    unique (source, article_id)
);

create table if not exists raw_filings (
    id            bigint generated always as identity primary key,
    source        text not null default 'sec_edgar',
    accession_no  text not null unique,
    ticker        text not null,
    form_type     text not null,              -- 10-K | 10-Q | 8-K
    filed_at      timestamptz not null,
    url           text,
    storage_path  text,                       -- full document in Storage (Phase 3)
    payload_json  jsonb,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now()
);

-- ============ CURATED ZONE ============

create table if not exists dim_ticker (
    id            bigint generated always as identity primary key,
    ticker        text not null,
    name          text,
    exchange      text,
    currency      text,
    sector        text,
    benchmark     text,
    tier          text not null check (tier in ('screening', 'analysis')),
    enabled       boolean not null default true,
    tags          text[],
    notes         text,
    valid_from    timestamptz not null default now(),
    ingested_at   timestamptz not null default now()
);
create index if not exists idx_dim_ticker_lookup
    on dim_ticker (ticker, ingested_at desc);

create table if not exists price_bars_daily (
    id            bigint generated always as identity primary key,
    ticker        text not null,
    bar_date      date not null,
    open          double precision not null,
    high          double precision not null,
    low           double precision not null,
    close         double precision not null,
    volume        bigint,
    source_used   text not null,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists idx_price_bars_lookup
    on price_bars_daily (ticker, bar_date desc, ingested_at desc);

create table if not exists corporate_actions (
    id            bigint generated always as identity primary key,
    ticker        text not null,
    ex_date       date not null,
    action_type   text not null,
    amount        double precision,
    ratio         double precision,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists idx_corp_actions_lookup
    on corporate_actions (ticker, ex_date desc);

create table if not exists indicators_daily (
    id            bigint generated always as identity primary key,
    ticker        text not null,
    bar_date      date not null,
    sma_10        double precision,
    sma_20        double precision,
    sma_50        double precision,
    sma_200       double precision,
    ema_10        double precision,
    ema_20        double precision,
    ema_50        double precision,
    ema_200       double precision,
    rsi_14        double precision,
    macd          double precision,
    macd_signal   double precision,
    macd_hist     double precision,
    atr_14        double precision,
    bb_upper      double precision,
    bb_middle     double precision,
    bb_lower      double precision,
    volume_ma_20  double precision,
    rel_strength_21d double precision,      -- ticker 21d return minus benchmark 21d return
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now(),
    unique (ticker, bar_date)                   -- rebuildable derived data; upsert-ignore
);
create index if not exists idx_indicators_lookup
    on indicators_daily (ticker, bar_date desc, ingested_at desc);

create table if not exists fundamentals_quarterly (
    id            bigint generated always as identity primary key,
    ticker        text not null,
    period_end    date not null,
    revenue       double precision,
    net_income    double precision,
    eps           double precision,
    total_debt    double precision,
    equity        double precision,
    gross_margin  double precision,
    net_margin    double precision,
    growth_yoy    double precision,         -- revenue growth vs same quarter prior year
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists idx_fundamentals_lookup
    on fundamentals_quarterly (ticker, period_end desc);

create table if not exists news_articles (        -- Phase 2
    id            bigint generated always as identity primary key,
    article_id    text not null unique,
    published_at  timestamptz not null,
    tickers       text[] not null default '{}',
    headline      text,
    sentiment_score double precision,
    storage_path  text,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists idx_news_articles_lookup
    on news_articles (published_at desc);

create table if not exists news_sentiment_daily ( -- Phase 2
    id            bigint generated always as identity primary key,
    ticker        text not null,
    date          date not null,
    sent_1d       double precision,
    sent_3d       double precision,
    sent_7d       double precision,
    article_count integer,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now(),
    unique (ticker, date)
);

create table if not exists catalyst_events (      -- Phase 3
    id            bigint generated always as identity primary key,
    ticker        text not null,
    event_date    date not null,
    event_type    text not null,              -- earnings | dividend | 8k
    details       jsonb,
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now()
);
create index if not exists idx_catalysts_lookup
    on catalyst_events (ticker, event_date);

create table if not exists screener_results (
    id            bigint generated always as identity primary key,
    result_date   date not null,
    ticker        text not null,
    screen_name   text not null,
    matched       boolean not null,
    metrics_json  jsonb not null,             -- computed metric values (transparency)
    valid_from    timestamptz not null,
    ingested_at   timestamptz not null default now(),
    unique (result_date, ticker, screen_name)       -- idempotent re-runs
);
create index if not exists idx_screener_results_lookup
    on screener_results (result_date desc, screen_name, matched);

create table if not exists research_packages (
    id            bigint generated always as identity primary key,
    ticker        text not null,
    snapshot_id   text not null,              -- shared across all feeds of one build cycle
    feed          text not null,              -- price_technicals | fundamentals | full | ...
    generated_at  timestamptz not null default now(),
    data_as_of    date not null,
    schema_version text not null default '1.0',
    payload_json  jsonb not null,
    ingested_at   timestamptz not null default now(),
    unique (ticker, snapshot_id, feed)
);
create index if not exists idx_packages_lookup
    on research_packages (ticker, data_as_of desc);

-- ============ OBSERVABILITY ============

create table if not exists pipeline_events (
    id            bigint generated always as identity primary key,
    run_id        text not null,              -- GitHub run id, or local uuid
    job_name      text not null,
    level         text not null check (level in ('INFO', 'WARN', 'ERROR')),
    event_type    text not null,              -- job_started | source_fetched | rows_written |
                                              -- divergence_detected | screen_matched |
                                              -- job_completed | job_failed | ...
    ticker        text,
    message       text,
    payload_json  jsonb,
    created_at    timestamptz not null default now()
);
create index if not exists idx_pipeline_events_lookup
    on pipeline_events (created_at desc, level, run_id);

-- ============ "CURRENT" VIEWS (latest ingested row per natural key) ============

create or replace view current_tickers as
select distinct on (ticker) * from dim_ticker
order by ticker, ingested_at desc;

create or replace view current_price_bars as
select distinct on (ticker, bar_date) * from price_bars_daily
order by ticker, bar_date, ingested_at desc;

create or replace view current_indicators as
select distinct on (ticker, bar_date) * from indicators_daily
order by ticker, bar_date, ingested_at desc;

create or replace view current_fundamentals as
select distinct on (ticker, period_end) * from fundamentals_quarterly
order by ticker, period_end, ingested_at desc;

create or replace view current_corporate_actions as
select distinct on (ticker, ex_date, action_type) * from corporate_actions
order by ticker, ex_date, action_type, ingested_at desc;
