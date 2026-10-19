-- 0021_finra_short_interest.sql — exchange short interest from FINRA (consolidated,
-- every listed stock, twice a month: mid-month and month-end settlement dates).
-- src/jobs/ingest_finra_short.py; the MCP's get_short_interest shows the history.
-- FINRA disseminates each settlement date about 7 business days later, so a past
-- read counts a row only once ingested or 12 days after its settlement date.
create table if not exists finra_short_interest (
    ticker              text not null,
    settlement_date     date not null,
    short_shares        bigint,
    prior_short_shares  bigint,
    change_pct          real,
    avg_daily_volume    bigint,
    days_to_cover       real,
    market              text,          -- FINRA market class (NYSE, NNM = Nasdaq Global Select...)
    run_id              text,
    ingested_at         timestamptz not null default now(),
    primary key (ticker, settlement_date)
);
create index if not exists idx_finra_short_date on finra_short_interest (settlement_date);
