-- 0009_macro_analysts_profiles.sql — macro context, analyst consensus, company profiles and peers.

-- FRED series (config/macro.yaml), as published; a revision overwrites the value.
create table if not exists macro_observations (
    series_id    text not null,
    obs_date     date not null,
    value        real not null,
    run_id       text,
    ingested_at  timestamptz not null default now(),
    primary key (series_id, obs_date)
);

-- Latest value per series and its change over 1 month, 3 months and 1 year
-- (difference in the series' own unit: percentage points for yields and spreads).
create or replace view macro_latest as
with last as (
    select distinct on (series_id) series_id, obs_date, value
    from macro_observations order by series_id, obs_date desc
)
select l.series_id, l.obs_date, l.value,
       l.value - (select m.value from macro_observations m where m.series_id = l.series_id
                  and m.obs_date <= l.obs_date - 30 order by m.obs_date desc limit 1)  as chg_1m,
       l.value - (select m.value from macro_observations m where m.series_id = l.series_id
                  and m.obs_date <= l.obs_date - 91 order by m.obs_date desc limit 1)  as chg_3m,
       l.value - (select m.value from macro_observations m where m.series_id = l.series_id
                  and m.obs_date <= l.obs_date - 365 order by m.obs_date desc limit 1) as chg_1y
from last l;

-- Finnhub /stock/recommendation: number of analysts per rating, one row per month.
create table if not exists analyst_recommendations (
    ticker       text not null,
    period       date not null,
    strong_buy   int, buy int, hold int, sell int, strong_sell int,
    run_id       text,
    ingested_at  timestamptz not null default now(),
    primary key (ticker, period)
);

-- Latest month per ticker: counts, a score from 1 (strong sell) to 5 (strong buy)
-- and the score three months earlier, so agents see the direction of revisions.
create or replace view analyst_consensus as
with scored as (
    select *, coalesce(strong_buy, 0) + coalesce(buy, 0) + coalesce(hold, 0)
              + coalesce(sell, 0) + coalesce(strong_sell, 0) as n_analysts,
           (5.0 * coalesce(strong_buy, 0) + 4 * coalesce(buy, 0) + 3 * coalesce(hold, 0)
            + 2 * coalesce(sell, 0) + coalesce(strong_sell, 0))
           / nullif(coalesce(strong_buy, 0) + coalesce(buy, 0) + coalesce(hold, 0)
                    + coalesce(sell, 0) + coalesce(strong_sell, 0), 0) as score
    from analyst_recommendations
),
last as (
    select distinct on (ticker) * from scored order by ticker, period desc
)
select l.ticker, l.period, l.n_analysts, l.strong_buy, l.buy, l.hold, l.sell, l.strong_sell,
       round(l.score::numeric, 2) as score,
       round(p.score::numeric, 2) as score_3m_ago,
       round((l.score - p.score)::numeric, 2) as score_chg_3m
from last l
left join scored p on p.ticker = l.ticker and p.period = (l.period - interval '3 months')::date;

-- Finnhub /stock/profile2 + /stock/peers, refreshed weekly for a third of the universe.
create table if not exists company_profiles (
    ticker              text primary key,
    name                text,
    country             text,
    currency            text,
    exchange            text,
    industry            text,      -- Finnhub industry (finnhubIndustry)
    ipo                 date,
    shares_outstanding  real,      -- millions
    weburl              text,
    peers               text[],    -- Finnhub peers, the ticker itself removed
    run_id              text,
    updated_at          timestamptz not null default now()
);

-- Each ticker's valuation and quality next to the median of its peers that we cover.
create or replace view peer_comparison as
with p as (
    select cp.ticker, unnest(cp.peers) as peer from company_profiles cp
),
med as (
    select p.ticker, count(*) as n_peers,
           percentile_cont(0.5) within group (order by s.pe_ttm) filter (where s.pe_ttm > 0) as peer_pe_ttm,
           percentile_cont(0.5) within group (order by s.ps_ttm)               as peer_ps_ttm,
           percentile_cont(0.5) within group (order by s.pb)                   as peer_pb,
           percentile_cont(0.5) within group (order by s.gross_margin_ttm)     as peer_gross_margin_ttm,
           percentile_cont(0.5) within group (order by s.operating_margin_ttm) as peer_operating_margin_ttm,
           percentile_cont(0.5) within group (order by s.revenue_growth_ttm_yoy) as peer_revenue_growth_ttm_yoy,
           percentile_cont(0.5) within group (order by s.roe_ttm)              as peer_roe_ttm
    from p join current_fundamentals_snapshot s on s.ticker = p.peer
    group by p.ticker
)
select cp.ticker, cp.industry, m.n_peers,
       s.pe_ttm, m.peer_pe_ttm, s.ps_ttm, m.peer_ps_ttm, s.pb, m.peer_pb,
       s.gross_margin_ttm, m.peer_gross_margin_ttm,
       s.operating_margin_ttm, m.peer_operating_margin_ttm,
       s.revenue_growth_ttm_yoy, m.peer_revenue_growth_ttm_yoy,
       s.roe_ttm, m.peer_roe_ttm
from company_profiles cp
left join med m using (ticker)
left join current_fundamentals_snapshot s using (ticker);
