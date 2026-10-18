-- 0014_street_data.sql — Yahoo street data (src/jobs/ingest_street.py, weekly):
-- EPS estimate trends and revisions, analyst price targets, short interest and
-- analyst rating changes. Filled gaps from the MCP sketch (2026-10-18).

-- One row per ticker, weekly snapshot and fiscal period
-- (0q = current quarter, +1q = next quarter, 0y = current year, +1y = next year).
-- eps_*_ago is the consensus EPS that many days before the snapshot;
-- up_/down_ count analysts who raised or cut their estimate in the window.
create table if not exists estimate_trends (
    ticker         text not null,
    snapshot_date  date not null,
    period         text not null,
    end_date       date,
    eps_avg        real, eps_low real, eps_high real,
    n_analysts     int,
    eps_growth     real,
    revenue_avg    double precision,
    eps_current    real, eps_7d_ago real, eps_30d_ago real, eps_60d_ago real, eps_90d_ago real,
    up_7d int, up_30d int, down_7d int, down_30d int,
    run_id         text,
    ingested_at    timestamptz not null default now(),
    primary key (ticker, snapshot_date, period)
);

-- One row per ticker and weekly snapshot: price targets and short interest.
-- short_pct_float is a fraction (0.05 = 5 %); short_ratio is days to cover.
create table if not exists street_snapshot (
    ticker                    text not null,
    snapshot_date             date not null,
    target_low                real, target_mean real, target_median real, target_high real,
    n_target_analysts         int,
    recommendation_mean       real,   -- Yahoo scale: 1 = strong buy ... 5 = sell
    shares_short              bigint,
    shares_short_prior_month  bigint,
    short_interest_date       date,
    short_pct_float           real,
    short_ratio               real,
    float_shares              bigint,
    run_id                    text,
    ingested_at               timestamptz not null default now(),
    primary key (ticker, snapshot_date)
);

-- One row per ticker, day and firm: upgrades, downgrades and price target moves (last year).
create table if not exists analyst_rating_changes (
    ticker                text not null,
    grade_date            date not null,
    firm                  text not null,
    to_grade              text,
    from_grade            text,
    action                text,   -- up, down, main, init, reit
    price_target_action   text,
    current_price_target  real,
    prior_price_target    real,
    run_id                text,
    ingested_at           timestamptz not null default now(),
    primary key (ticker, grade_date, firm)
);

-- Latest snapshot per ticker and period, with the consensus EPS change over
-- 7, 30 and 90 days (fraction of the old estimate) and net analyst revisions.
create or replace view estimate_revisions as
select distinct on (ticker, period)
       ticker, period, snapshot_date, end_date, n_analysts, eps_current,
       round(((eps_current - eps_7d_ago)  / nullif(abs(eps_7d_ago), 0))::numeric, 4)  as eps_chg_7d,
       round(((eps_current - eps_30d_ago) / nullif(abs(eps_30d_ago), 0))::numeric, 4) as eps_chg_30d,
       round(((eps_current - eps_90d_ago) / nullif(abs(eps_90d_ago), 0))::numeric, 4) as eps_chg_90d,
       up_30d, down_30d, coalesce(up_30d, 0) - coalesce(down_30d, 0) as net_revisions_30d,
       eps_low, eps_high, eps_growth, revenue_avg
from estimate_trends
order by ticker, period, snapshot_date desc;

-- Latest snapshot per ticker, with upside to the mean target from the last close
-- and the change in shares short against the prior month.
create or replace view street_latest as
with last as (
    select distinct on (ticker) * from street_snapshot order by ticker, snapshot_date desc
)
select l.ticker, l.snapshot_date, p.bar_date as close_date, p.close as last_close,
       l.target_low, l.target_mean, l.target_median, l.target_high, l.n_target_analysts,
       round((l.target_mean / nullif(p.close, 0) - 1)::numeric, 4) as target_upside,
       l.recommendation_mean,
       l.short_interest_date, l.shares_short, l.short_pct_float, l.short_ratio,
       round((l.shares_short::numeric / nullif(l.shares_short_prior_month, 0) - 1), 4) as short_chg_1m,
       l.float_shares
from last l
left join lateral (
    select b.bar_date, b.close from price_bars_daily b
    where b.ticker = l.ticker order by b.bar_date desc, b.ingested_at desc limit 1
) p on true;
