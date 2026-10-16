-- Finnhub insider sentiment: one row per (ticker, month, version).
-- The current month is revised as Form 4s arrive, so the natural key includes
-- the values: identical re-fetches are no-ops, a revision is a new row with a
-- later valid_from. NULLS NOT DISTINCT keeps months with missing values idempotent.
create table if not exists raw_insider_sentiment (
    id            bigint generated always as identity primary key,
    source        text not null default 'finnhub',
    ticker        text not null,
    period        date not null,              -- first day of the month
    year          integer not null,
    month         integer not null,
    change        double precision,           -- net shares bought (+) / sold (-)
    mspr          double precision,           -- Monthly Share Purchase Ratio, -100..100
    payload_json  jsonb,
    valid_from    timestamptz not null default now(),
    ingested_at   timestamptz not null default now(),
    constraint raw_insider_sentiment_natural_key
        unique nulls not distinct (source, ticker, period, change, mspr)
);
create index if not exists idx_raw_insider_sentiment_lookup
    on raw_insider_sentiment (ticker, period desc);
