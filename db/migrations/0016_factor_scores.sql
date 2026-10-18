-- 0016_factor_scores.sql — daily factor scores for the stock screen
-- (src/jobs/compute_factors.py, src/transforms/factors.py). One row per ticker and
-- day, rebuilt as of each day's close; daily rows kept 40 days, month-ends for good.
-- Raw inputs are stored next to the z-scores so every number can be traced.

create table if not exists factor_scores (
    as_of           date not null,
    ticker          text not null,
    industry        text,
    eligible        boolean not null,     -- fresh price and >= 1M USD a day traded
    last_bar_date   date,
    adv_20d         double precision,     -- average dollar volume, 20 days
    mom_12_1        real,                 -- return 12 months ago -> 1 month ago
    rev_chg         real,                 -- EPS estimate change, 30/90 days, this + next year
    rev_breadth     real,                 -- (ups - downs) / analysts, 30 days
    sue             real,                 -- last EPS surprise (fraction), if recent
    ear             real,                 -- price reaction around the last report
    ins_buyers      int,                  -- insiders buying in the open market, 90 days
    ins_buy_value   double precision,
    insider_raw     real,
    roe_ttm         real,
    gross_margin    real,
    debt_to_equity  real,
    op_margin_std   real,                 -- volatility of operating margin, 8 quarters
    z_momentum      real,
    z_revisions     real,
    z_pead          real,
    z_insider       real,
    z_quality       real,
    score           real,                 -- mean of the five z-scores (missing = 0)
    n_factors       int,
    rank            int,                  -- 1 = best among eligible on that day
    run_id          text,
    computed_at     timestamptz not null default now(),
    primary key (as_of, ticker)
);
create index if not exists idx_factor_scores_ticker on factor_scores (ticker, as_of desc);

-- The latest day's eligible stocks, best first.
create or replace view factor_latest as
select * from factor_scores
where as_of = (select max(as_of) from factor_scores) and eligible
order by rank;
