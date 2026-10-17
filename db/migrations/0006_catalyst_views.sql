-- 0006_catalyst_views.sql — curated catalyst views over raw tables we already ingest
-- (earnings calendar and surprises, SEC filings, splits and dividends). Views only:
-- nothing is stored twice. `catalysts` is one row per enabled ticker, the input of
-- the Especialista (catalysts) feed.

-- A rescheduled report leaves its old date behind: keep the newest row per quarter.
create or replace view current_earnings_calendar as
select distinct on (ticker, year, quarter) * from raw_earnings_calendar
order by ticker, year, quarter, ingested_at desc;

create or replace view current_earnings_surprises as
select distinct on (ticker, period) * from raw_earnings_surprises
order by ticker, period, ingested_at desc;

create or replace view current_corporate_actions as
select distinct on (ticker, ex_date, action_type) * from raw_corporate_actions
order by ticker, ex_date, action_type, ingested_at desc;

create or replace view catalysts as
with t as (
    select ticker from current_tickers where enabled
),
next_er as (
    select distinct on (ticker) ticker, event_date, hour, eps_estimate, revenue_estimate
    from current_earnings_calendar where event_date >= current_date
    order by ticker, event_date
),
surprises as (
    select ticker,
           (array_agg(surprise_pct order by period desc))[1]           as last_surprise_pct,
           (array_agg(period order by period desc))[1]                 as last_surprise_period,
           count(*) filter (where surprise > 0)                        as beats_last_4,
           avg(surprise_pct)                                           as avg_surprise_pct_last_4
    from (
        select *, row_number() over (partition by ticker order by period desc) rn
        from current_earnings_surprises
    ) s
    where rn <= 4
    group by ticker
),
filings as (
    select ticker,
           max(filed_at) filter (where form_type = '8-K')                                    as last_8k_at,
           count(*) filter (where form_type = '8-K' and filed_at >= now() - interval '30 days') as n_8k_30d,
           count(*) filter (where form_type = '4' and filed_at >= now() - interval '30 days')   as n_form4_30d,
           count(*) filter (where form_type = '144' and filed_at >= now() - interval '30 days') as n_form144_30d
    from raw_filings
    where filed_at >= now() - interval '1 year'
    group by ticker
),
actions as (
    select ticker,
           max(ex_date) filter (where action_type = 'dividend' and ex_date <= current_date) as last_dividend_ex_date,
           (array_agg(amount order by ex_date desc) filter (where action_type = 'dividend' and ex_date <= current_date))[1]
                                                                                       as last_dividend_amount,
           min(ex_date) filter (where action_type = 'dividend' and ex_date > current_date)  as next_dividend_ex_date,
           max(ex_date) filter (where action_type = 'split')                                as last_split_date,
           (array_agg(ratio order by ex_date desc) filter (where action_type = 'split'))[1] as last_split_ratio
    from current_corporate_actions
    group by ticker
)
select t.ticker,
       n.event_date                      as next_earnings_date,
       n.event_date - current_date       as days_to_earnings,
       n.hour                            as next_earnings_hour,
       n.eps_estimate                    as next_eps_estimate,
       n.revenue_estimate                as next_revenue_estimate,
       s.last_surprise_period, s.last_surprise_pct, s.beats_last_4, s.avg_surprise_pct_last_4,
       f.last_8k_at, coalesce(f.n_8k_30d, 0) as n_8k_30d,
       coalesce(f.n_form4_30d, 0) as n_form4_30d, coalesce(f.n_form144_30d, 0) as n_form144_30d,
       a.last_dividend_ex_date, a.last_dividend_amount, a.next_dividend_ex_date,
       a.last_split_date, a.last_split_ratio
from t
left join next_er  n using (ticker)
left join surprises s using (ticker)
left join filings  f using (ticker)
left join actions  a using (ticker);
