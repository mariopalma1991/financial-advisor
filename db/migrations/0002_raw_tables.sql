-- 0002_raw_tables.sql — new raw tables for Finnhub adapters.
-- Apply in Supabase SQL editor after 0001_init.sql.

-- ============ RAW ZONE — FINNHUB FEEDS ============

-- Financials as reported (balance sheet, cash flow, income statement)
create table if not exists raw_financial_reports (
    id            bigint generated always as identity primary key,
    source        text not null default 'finnhub',
    ticker        text not null,
    cik           text,
    access_number text not null,
    form          text not null,              -- 10-K | 10-Q
    year          integer not null,
    quarter       integer not null,           -- 0 = annual, 1-4 = fiscal quarter
    start_date    date,
    end_date      date,
    filed_date    timestamptz,
    report_json   jsonb not null,             -- {"bs": {...}, "cf": {...}, "ic": {...}}
    payload_json  jsonb,
    valid_from    timestamptz not null default now(),
    ingested_at   timestamptz not null default now(),
    unique (source, ticker, access_number)
);
create index if not exists idx_raw_fin_reports_lookup
    on raw_financial_reports (ticker, end_date desc);

-- Earnings surprises (actual vs estimate)
create table if not exists raw_earnings_surprises (
    id            bigint generated always as identity primary key,
    source        text not null default 'finnhub',
    ticker        text not null,
    period        date not null,              -- period end date
    quarter       integer not null,
    year          integer not null,
    actual        double precision,
    estimate      double precision,
    surprise      double precision,
    surprise_pct  double precision,
    payload_json  jsonb,
    valid_from    timestamptz not null default now(),
    ingested_at   timestamptz not null default now(),
    unique (source, ticker, period)
);
create index if not exists idx_raw_earnings_lookup
    on raw_earnings_surprises (ticker, period desc);

-- Earnings calendar (upcoming and historical releases)
create table if not exists raw_earnings_calendar (
    id            bigint generated always as identity primary key,
    source        text not null default 'finnhub',
    ticker        text not null,
    event_date    date not null,
    quarter       integer not null,
    year          integer not null,
    hour          text,                       -- bmo | amc | dmh
    eps_actual    double precision,
    eps_estimate  double precision,
    revenue_actual   double precision,
    revenue_estimate double precision,
    payload_json  jsonb,
    valid_from    timestamptz not null default now(),
    ingested_at   timestamptz not null default now(),
    unique (source, ticker, event_date, quarter)
);
create index if not exists idx_raw_earn_cal_lookup
    on raw_earnings_calendar (ticker, event_date desc);

-- H1-B visa applications
create table if not exists raw_visa_applications (
    id            bigint generated always as identity primary key,
    source        text not null default 'finnhub',
    ticker        text not null,
    case_number   text not null,
    job_title     text,
    visa_class    text,
    case_status   text,
    employer_name text,
    begin_date    date,
    end_date      date,
    received_date date,
    quarter       integer,
    year          integer,
    wage_range_from  double precision,
    wage_range_to    double precision,
    wage_unit     text,
    worksite_city text,
    worksite_state text,
    full_time     boolean,
    h1b_dependent boolean,
    payload_json  jsonb,
    valid_from    timestamptz not null default now(),
    ingested_at   timestamptz not null default now(),
    unique (source, ticker, case_number)
);
create index if not exists idx_raw_visa_lookup
    on raw_visa_applications (ticker, received_date desc);