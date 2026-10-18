-- 0020_insider_history_and_ratings.sql — history for the two factors that had none.
--
-- insider_purchases: open-market insider buys (Form 4, code P) from SEC's quarterly
-- Insider Transactions Data Sets (src/jobs/backfill_insider_purchases.py). Small:
-- buys only. compute_factors reads it for days before insider_trades starts.
create table if not exists insider_purchases (
    accession         text not null,
    ticker            text not null,
    filed_at          date not null,        -- the day the Form 4 became public
    first_trade_date  date,
    owner             text,
    role              text,
    is_10b5_1         boolean,              -- null when the data set has no flag
    buy_value         double precision not null,   -- USD, sum of code-P rows
    quarter           text not null,        -- data set it came from, e.g. 2025q1
    run_id            text,
    ingested_at       timestamptz not null default now(),
    primary key (accession, ticker)
);
create index if not exists idx_insider_purchases_ticker on insider_purchases (ticker, filed_at);

-- Revisions factor: analyst price-target changes and up/downgrades (analyst_rating_changes).
alter table factor_scores add column if not exists tgt_chg real;          -- mean price-target change, 90 days
alter table factor_scores add column if not exists rating_breadth real;   -- (upgrades - downgrades) / actions, 90 days
alter table factor_scores add column if not exists n_rating_actions int;

-- select * views keep the columns they were created with; this appends the new ones
create or replace view factor_latest as
select * from factor_scores
where as_of = (select max(as_of) from factor_scores) and eligible
order by rank;
