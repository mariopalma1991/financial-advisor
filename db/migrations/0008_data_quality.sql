-- 0008_data_quality.sql — news relevance and per-ticker data quality flags.

-- Name/ticker patterns per ticker (src/transforms/ticker_aliases.py), rebuilt by
-- ingest_news from the curated universe.
create table if not exists ticker_aliases (
    ticker          text not null,
    pattern         text not null,      -- Postgres regular expression
    case_sensitive  boolean not null,
    primary key (ticker, pattern)
);

-- Per-ticker news reads (the view below, feeds) filter on the Finnhub ticker tag.
create index if not exists idx_raw_news_related
    on raw_news_articles ((payload_json ->> 'related'), published_at desc);

-- Articles that are actually about the ticker Finnhub tagged them with: the headline
-- or summary matches one of its aliases. Syndicated copies (same headline for the
-- same ticker) are collapsed to the earliest one.
create or replace view news_relevant as
select distinct on (n.payload_json ->> 'related', lower(n.headline))
       n.id, n.payload_json ->> 'related' as ticker, n.published_at, n.headline,
       n.payload_json ->> 'summary' as summary, n.payload_json ->> 'source' as publisher,
       n.url, n.article_id, n.ingested_at
from raw_news_articles n
where exists (
    select 1 from ticker_aliases a
    where a.ticker = n.payload_json ->> 'related'
      and case when a.case_sensitive
               then (n.headline || ' ' || coalesce(n.payload_json ->> 'summary', '')) ~ a.pattern
               else (n.headline || ' ' || coalesce(n.payload_json ->> 'summary', '')) ~* a.pattern
          end
)
order by n.payload_json ->> 'related', lower(n.headline), n.published_at;

-- 6-K is the foreign-issuer 8-K (ADRs on the SIC): count both as material events.
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
           max(filed_at) filter (where form_type in ('8-K', '6-K'))                                    as last_8k_at,
           count(*) filter (where form_type in ('8-K', '6-K') and filed_at >= now() - interval '30 days') as n_8k_30d,
           count(*) filter (where form_type = '4' and filed_at >= now() - interval '30 days')           as n_form4_30d,
           count(*) filter (where form_type = '144' and filed_at >= now() - interval '30 days')         as n_form144_30d
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

-- One row per problem found in a ticker's processed data. Agents should treat a
-- flagged number with caution; monitor_health reports the counts per check.
--   severity: error = the data is wrong or missing, warn = suspicious, info = gap.
create or replace view data_quality_flags as
with t as (select ticker from current_tickers where enabled),
last_bar as (   -- one index probe per ticker instead of a pass over every bar
    select t.ticker, (select max(p.bar_date) from price_bars_daily p where p.ticker = t.ticker) as bar_date
    from t
),
recent as (
    select ticker, bar_date, open, high, low, close, volume,
           close / nullif(lag(close) over (partition by ticker order by bar_date), 0) - 1 as ret
    from current_price_bars where bar_date >= current_date - 45
)
-- prices
select t.ticker, 'stale_prices' as check_name, 'error' as severity,
       'last bar ' || lb.bar_date as detail
from t join last_bar lb using (ticker)
where lb.bar_date < current_date - 4
union all
select ticker, 'bad_bar', 'warn',
       bar_date || ': ' || case when coalesce(open, 1) <= 0 or coalesce(high, 1) <= 0 or coalesce(low, 1) <= 0
                                then 'non-positive open/high/low' else 'zero volume' end
from recent
where bar_date >= current_date - 30 and (coalesce(open, 1) <= 0 or coalesce(high, 1) <= 0
      or coalesce(low, 1) <= 0 or volume = 0)
union all
select r.ticker, 'jump_without_split', 'warn',
       r.bar_date || ': ' || round((r.ret * 100)::numeric, 1) || '% in one day, no split on record'
from recent r
where r.bar_date >= current_date - 30 and abs(r.ret) > 0.4
  and not exists (select 1 from raw_corporate_actions a
                  where a.ticker = r.ticker and a.action_type = 'split'
                    and a.ex_date between r.bar_date - 3 and r.bar_date + 3)
-- indicators
union all
select t.ticker, 'indicators_behind', 'warn',
       'indicators ' || coalesce(max(i.bar_date)::text, 'missing') || ', prices ' || lb.bar_date
from t join last_bar lb using (ticker)
left join indicators_daily i on i.ticker = t.ticker and i.bar_date >= current_date - 30
group by t.ticker, lb.bar_date
having coalesce(max(i.bar_date), date '1900-01-01') < lb.bar_date
-- fundamentals
union all
select t.ticker, 'fundamentals_stale', 'info',
       coalesce('snapshot ' || s.as_of, 'no snapshot')
from t left join current_fundamentals_snapshot s using (ticker)
where s.as_of is null or s.as_of < current_date - 10
-- news
union all
select t.ticker, 'no_relevant_news_30d', 'info', 'no article about the company in 30 days'
from t
where not exists (
    select 1 from raw_news_articles n
    where n.payload_json ->> 'related' = t.ticker and n.published_at >= now() - interval '30 days'
      and exists (select 1 from ticker_aliases a
                  where a.ticker = t.ticker
                    and case when a.case_sensitive
                             then (n.headline || ' ' || coalesce(n.payload_json ->> 'summary', '')) ~ a.pattern
                             else (n.headline || ' ' || coalesce(n.payload_json ->> 'summary', '')) ~* a.pattern
                        end))
-- earnings
union all
select c.ticker, 'earnings_actual_missing', 'info',
       'reported ' || c.event_date || ', no actual EPS yet'
from current_earnings_calendar c join t using (ticker)
where c.event_date between current_date - 14 and current_date - 2 and c.eps_actual is null;
