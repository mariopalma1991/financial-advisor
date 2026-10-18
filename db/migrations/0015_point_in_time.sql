-- 0015_point_in_time.sql — "as of" versions of the computed views, so the MCP tools
-- (and the validator) can rebuild what was known at a past moment. Same logic as the
-- views in 0006, 0009 and 0011, with now()/current_date replaced by p_at and each
-- source filtered to rows known by then:
--   filings by filed_at; earnings reports by event_date (an actual exists only after it);
--   surprises 45 days after the quarter end (when the report is out), or once ingested;
--   splits and dividends by ex_date; fundamentals snapshots by their as_of date.
-- Where a row was re-ingested, the version stored by p_at wins; rows first loaded
-- after p_at (backfills) fall back to the latest copy (the tools say so in a note).

create or replace function catalysts_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, next_earnings_date date, days_to_earnings int, next_earnings_hour text,
    next_eps_estimate double precision, next_revenue_estimate double precision,
    last_surprise_period date, last_surprise_pct double precision, beats_last_4 bigint, avg_surprise_pct_last_4 double precision,
    last_8k_at timestamptz, n_8k_30d bigint, n_form4_30d bigint, n_form144_30d bigint,
    last_dividend_ex_date date, last_dividend_amount double precision, next_dividend_ex_date date,
    last_split_date date, last_split_ratio double precision
) language sql stable as $$
with d as (select p_at::date as day),
cal as (
    select distinct on (year, quarter) * from raw_earnings_calendar c
    where c.ticker = p_ticker order by year, quarter, (ingested_at <= p_at) desc, ingested_at desc
),
next_er as (
    select event_date, hour, eps_estimate, revenue_estimate from cal, d
    where event_date >= d.day order by event_date limit 1
),
s4 as (
    select distinct on (period) * from raw_earnings_surprises s
    where s.ticker = p_ticker and (s.period + 45 <= p_at::date or s.ingested_at <= p_at)
    order by period desc, (ingested_at <= p_at) desc, ingested_at desc limit 4
),
surprises as (
    select (array_agg(surprise_pct order by period desc))[1] as last_surprise_pct,
           (array_agg(period order by period desc))[1]       as last_surprise_period,
           count(*) filter (where surprise > 0)              as beats_last_4,
           avg(surprise_pct)                                 as avg_surprise_pct_last_4
    from s4
),
filings as (
    select max(filed_at) filter (where form_type = '8-K')                                         as last_8k_at,
           count(*) filter (where form_type = '8-K' and filed_at >= p_at - interval '30 days')  as n_8k_30d,
           count(*) filter (where form_type = '4' and filed_at >= p_at - interval '30 days')    as n_form4_30d,
           count(*) filter (where form_type = '144' and filed_at >= p_at - interval '30 days')  as n_form144_30d
    from raw_filings f
    where f.ticker = p_ticker and f.filed_at <= p_at and f.filed_at >= p_at - interval '1 year'
),
acts as (
    select distinct on (ex_date, action_type) * from raw_corporate_actions a
    where a.ticker = p_ticker order by ex_date, action_type, ingested_at desc
),
actions as (
    select max(ex_date) filter (where action_type = 'dividend' and ex_date <= p_at::date) as last_dividend_ex_date,
           (array_agg(amount order by ex_date desc)
               filter (where action_type = 'dividend' and ex_date <= p_at::date))[1]      as last_dividend_amount,
           min(ex_date) filter (where action_type = 'dividend' and ex_date > p_at::date)  as next_dividend_ex_date,
           max(ex_date) filter (where action_type = 'split' and ex_date <= p_at::date)    as last_split_date,
           (array_agg(ratio order by ex_date desc)
               filter (where action_type = 'split' and ex_date <= p_at::date))[1]         as last_split_ratio
    from acts
)
select p_ticker, n.event_date, n.event_date - p_at::date, n.hour, n.eps_estimate, n.revenue_estimate,
       s.last_surprise_period, s.last_surprise_pct, s.beats_last_4, s.avg_surprise_pct_last_4,
       f.last_8k_at, coalesce(f.n_8k_30d, 0), coalesce(f.n_form4_30d, 0), coalesce(f.n_form144_30d, 0),
       a.last_dividend_ex_date, a.last_dividend_amount, a.next_dividend_ex_date,
       a.last_split_date, a.last_split_ratio
from surprises s cross join filings f cross join actions a left join next_er n on true
$$;

create or replace function insider_activity_30d_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, n_buy_filings bigint, n_buyers bigint, buy_value real,
    discretionary_sell_value real, plan_sell_value real
) language sql stable as $$
select p_ticker,
       count(*) filter (where buy_value > 0),
       count(distinct owner) filter (where buy_value > 0),
       coalesce(sum(buy_value), 0),
       coalesce(sum(sell_value) filter (where not coalesce(is_10b5_1, false)), 0),
       coalesce(sum(sell_value) filter (where is_10b5_1), 0)
from insider_trades
where ticker = p_ticker and filed_at <= p_at and filed_at > p_at - interval '30 days'
$$;

-- Peers come from today's company_profiles (profiles are not versioned); their
-- metrics come from the fundamentals snapshot each peer had at p_at.
create or replace function peer_comparison_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, snapshot_as_of date, industry text, n_peers bigint,
    pe_ttm real, peer_pe_ttm double precision, ps_ttm real, peer_ps_ttm double precision,
    pb real, peer_pb double precision, gross_margin_ttm real, peer_gross_margin_ttm double precision,
    operating_margin_ttm real, peer_operating_margin_ttm double precision,
    revenue_growth_ttm_yoy real, peer_revenue_growth_ttm_yoy double precision,
    roe_ttm real, peer_roe_ttm double precision
) language sql stable as $$
with snap as (
    select distinct on (s.ticker) s.* from fundamentals_snapshot s
    where s.as_of <= p_at::date
      and s.ticker in (select p_ticker union select unnest(peers) from company_profiles where ticker = p_ticker)
    order by s.ticker, s.as_of desc
),
med as (
    select count(*) as n_peers,
           percentile_cont(0.5) within group (order by s.pe_ttm) filter (where s.pe_ttm > 0) as peer_pe_ttm,
           percentile_cont(0.5) within group (order by s.ps_ttm)               as peer_ps_ttm,
           percentile_cont(0.5) within group (order by s.pb)                   as peer_pb,
           percentile_cont(0.5) within group (order by s.gross_margin_ttm)     as peer_gross_margin_ttm,
           percentile_cont(0.5) within group (order by s.operating_margin_ttm) as peer_operating_margin_ttm,
           percentile_cont(0.5) within group (order by s.revenue_growth_ttm_yoy) as peer_revenue_growth_ttm_yoy,
           percentile_cont(0.5) within group (order by s.roe_ttm)              as peer_roe_ttm
    from snap s where s.ticker <> p_ticker
)
select cp.ticker, me.as_of, cp.industry, m.n_peers,
       me.pe_ttm, m.peer_pe_ttm, me.ps_ttm, m.peer_ps_ttm, me.pb, m.peer_pb,
       me.gross_margin_ttm, m.peer_gross_margin_ttm, me.operating_margin_ttm, m.peer_operating_margin_ttm,
       me.revenue_growth_ttm_yoy, m.peer_revenue_growth_ttm_yoy, me.roe_ttm, m.peer_roe_ttm
from company_profiles cp
cross join med m
left join snap me on me.ticker = cp.ticker
where cp.ticker = p_ticker
$$;

-- Analyst rating counts: monthly periods are published at the start of the month.
create or replace function analyst_consensus_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, period date, n_analysts int, strong_buy int, buy int, hold int, sell int, strong_sell int,
    score numeric, score_3m_ago numeric, score_chg_3m numeric
) language sql stable as $$
with scored as (
    select distinct on (r.period) r.period, r.strong_buy, r.buy, r.hold, r.sell, r.strong_sell,
           coalesce(r.strong_buy, 0) + coalesce(r.buy, 0) + coalesce(r.hold, 0)
           + coalesce(r.sell, 0) + coalesce(r.strong_sell, 0) as n_analysts,
           (5.0 * coalesce(r.strong_buy, 0) + 4 * coalesce(r.buy, 0) + 3 * coalesce(r.hold, 0)
            + 2 * coalesce(r.sell, 0) + coalesce(r.strong_sell, 0))
           / nullif(coalesce(r.strong_buy, 0) + coalesce(r.buy, 0) + coalesce(r.hold, 0)
                    + coalesce(r.sell, 0) + coalesce(r.strong_sell, 0), 0) as score
    from analyst_recommendations r
    where r.ticker = p_ticker and r.period <= p_at::date
    order by r.period, (r.ingested_at <= p_at) desc, r.ingested_at desc
),
last as (select * from scored order by period desc limit 1)
select p_ticker, l.period, l.n_analysts::int, l.strong_buy, l.buy, l.hold, l.sell, l.strong_sell,
       round(l.score::numeric, 2), round(p.score::numeric, 2), round((l.score - p.score)::numeric, 2)
from last l
left join scored p on p.period = (l.period - interval '3 months')::date
$$;

-- Yahoo street data: the weekly snapshot taken by p_at, with the close known then.
create or replace function street_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, snapshot_date date, close_date date, last_close double precision,
    target_low real, target_mean real, target_median real, target_high real, n_target_analysts int,
    target_upside numeric, recommendation_mean real,
    short_interest_date date, shares_short bigint, short_pct_float real, short_ratio real,
    short_chg_1m numeric, float_shares bigint
) language sql stable as $$
with l as (
    select * from street_snapshot s
    where s.ticker = p_ticker and s.snapshot_date <= p_at::date
    order by s.snapshot_date desc limit 1
),
p as (
    select b.bar_date, b.close from price_bars_daily b
    where b.ticker = p_ticker and b.valid_from <= p_at
    order by b.bar_date desc, b.ingested_at desc limit 1
)
select l.ticker, l.snapshot_date, p.bar_date, p.close,
       l.target_low, l.target_mean, l.target_median, l.target_high, l.n_target_analysts,
       round((l.target_mean / nullif(p.close, 0) - 1)::numeric, 4), l.recommendation_mean,
       l.short_interest_date, l.shares_short, l.short_pct_float, l.short_ratio,
       round((l.shares_short::numeric / nullif(l.shares_short_prior_month, 0) - 1), 4), l.float_shares
from l left join p on true
$$;

create or replace function estimate_revisions_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, period text, snapshot_date date, end_date date, n_analysts int, eps_current real,
    eps_chg_7d numeric, eps_chg_30d numeric, eps_chg_90d numeric,
    up_30d int, down_30d int, net_revisions_30d int,
    eps_low real, eps_high real, eps_growth real, revenue_avg double precision
) language sql stable as $$
select distinct on (e.period)
       e.ticker, e.period, e.snapshot_date, e.end_date, e.n_analysts, e.eps_current,
       round(((e.eps_current - e.eps_7d_ago)  / nullif(abs(e.eps_7d_ago), 0))::numeric, 4),
       round(((e.eps_current - e.eps_30d_ago) / nullif(abs(e.eps_30d_ago), 0))::numeric, 4),
       round(((e.eps_current - e.eps_90d_ago) / nullif(abs(e.eps_90d_ago), 0))::numeric, 4),
       e.up_30d, e.down_30d, coalesce(e.up_30d, 0) - coalesce(e.down_30d, 0),
       e.eps_low, e.eps_high, e.eps_growth, e.revenue_avg
from estimate_trends e
where e.ticker = p_ticker and e.snapshot_date <= p_at::date
order by e.period, e.snapshot_date desc
$$;

-- FRED series as published by p_at: an observation counts once its usual release
-- lag has passed (CPI ~mid next month, unemployment the first Friday after the month,
-- jobless claims the Thursday after the week, market series the next day).
create or replace function macro_at(p_at timestamptz, p_series text[] default null)
returns table (series_id text, obs_date date, value real,
               chg_1m real, chg_3m real, chg_1y real)
language sql stable as $$
with last as (
    select distinct on (o.series_id) o.series_id, o.obs_date, o.value
    from macro_observations o
    where o.obs_date + case o.series_id when 'CPIAUCSL' then 45 when 'UNRATE' then 37
                                        when 'ICSA' then 5 else 1 end <= p_at::date
      and (p_series is null or o.series_id = any(p_series))
    order by o.series_id, o.obs_date desc
)
select l.series_id, l.obs_date, l.value,
       l.value - (select m.value from macro_observations m where m.series_id = l.series_id
                  and m.obs_date <= l.obs_date - 30 order by m.obs_date desc limit 1),
       l.value - (select m.value from macro_observations m where m.series_id = l.series_id
                  and m.obs_date <= l.obs_date - 91 order by m.obs_date desc limit 1),
       l.value - (select m.value from macro_observations m where m.series_id = l.series_id
                  and m.obs_date <= l.obs_date - 365 order by m.obs_date desc limit 1)
from last l
order by l.series_id
$$;
