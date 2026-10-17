-- 0007_fundamentals.sql — fundamentals from Finnhub /stock/metric (src/jobs/ingest_fundamentals.py).
-- Replaces the EDGAR-based fundamentals_quarterly of 0001 (never created in Supabase).
--
-- fundamentals_snapshot: one row per ticker per weekly run (valuation, margins TTM,
--   growth, leverage). Ratios are fractions (0.25 = 25 %); market_cap in USD millions.
-- fundamentals_quarterly: last 12 quarters of per-quarter ratios. Versioned like the
--   other curated tables: a quarter is re-inserted only when Finnhub revises a value,
--   and valid_from is when we first saw that version (point-in-time reads use it).

drop view if exists current_fundamentals;
drop table if exists fundamentals_quarterly;

create table fundamentals_snapshot (
    id                     bigint generated always as identity primary key,
    ticker                 text not null,
    as_of                  date not null,
    market_cap             real, beta real,
    pe_ttm real, pb real, ps_ttm real, pfcf_ttm real, dividend_yield real,
    eps_ttm                real,
    eps_growth_ttm_yoy real, eps_growth_q_yoy real,
    revenue_growth_ttm_yoy real, revenue_growth_q_yoy real, revenue_growth_3y real,
    gross_margin_ttm real, operating_margin_ttm real, net_margin_ttm real,
    roe_ttm real, roa_ttm real,
    debt_to_equity real, current_ratio real,
    high_52w real, low_52w real,
    run_id                 text,
    ingested_at            timestamptz not null default now(),
    unique (ticker, as_of)
);

create table fundamentals_quarterly (
    id                     bigint generated always as identity primary key,
    ticker                 text not null,
    period_end             date not null,
    eps real, sales_per_share real, book_value_per_share real,
    gross_margin real, operating_margin real, net_margin real, fcf_margin real,
    roe_ttm real, roa_ttm real, debt_to_equity real, current_ratio real,
    pe_ttm real, pb real, ps_ttm real,
    growth_yoy             real,      -- sales per share vs the same quarter a year earlier
    valid_from             timestamptz not null,
    run_id                 text,
    ingested_at            timestamptz not null default now()
);
create index idx_fundamentals_lookup on fundamentals_quarterly (ticker, period_end desc);

create view current_fundamentals as
select distinct on (ticker, period_end) * from fundamentals_quarterly
order by ticker, period_end, ingested_at desc;

create view current_fundamentals_snapshot as
select distinct on (ticker) * from fundamentals_snapshot
order by ticker, as_of desc;
