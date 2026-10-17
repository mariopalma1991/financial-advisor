-- 0011_filing_enrichment.sql — 8-K/6-K events and Form 4 trades read from EDGAR
-- by enrich_filings (results in raw_filings.payload_json).

-- 8-K / 6-K: item codes (src/adapters/sec_edgar.py ITEM_LABELS) and FinBERT sentiment
-- of the press release or main document. Full text: archive filings_text.
create or replace view filing_events as
select f.id, f.ticker, f.form_type, f.filed_at, f.url,
       array(select jsonb_array_elements_text(f.payload_json -> 'items')) as items,
       (f.payload_json ->> 'sentiment')::real                           as sentiment,
       f.payload_json -> 'items' ? '2.02'                                as is_results,
       f.payload_json -> 'items' ?| array['1.03', '2.04', '2.06', '3.01', '4.02'] as is_red_flag,
       f.payload_json -> 'items' ? '5.02'                                as is_leadership_change
from raw_filings f
where f.form_type in ('8-K', '8-K/A', '6-K', '6-K/A') and f.payload_json ? 'enriched';

-- Form 4: one row per filing. Open-market buys (code P) are the informative trades;
-- sells under a 10b5-1 plan are pre-scheduled and carry little timing signal.
create or replace view insider_trades as
select f.id, f.ticker, f.filed_at, f.url,
       f.payload_json ->> 'owner'                          as owner,
       f.payload_json ->> 'role'                           as role,
       (f.payload_json ->> 'is_10b5_1')::boolean           as is_10b5_1,
       array(select jsonb_array_elements_text(f.payload_json -> 'codes')) as codes,
       (f.payload_json ->> 'buy_value')::real              as buy_value,
       (f.payload_json ->> 'sell_value')::real             as sell_value,
       (f.payload_json ->> 'net_shares')::real             as net_shares
from raw_filings f
where f.form_type in ('4', '4/A') and f.payload_json ? 'enriched';

-- Per ticker, last 30 days: open-market insider buying vs discretionary selling.
create or replace view insider_activity_30d as
select ticker,
       count(*) filter (where buy_value > 0)                               as n_buy_filings,
       count(distinct owner) filter (where buy_value > 0)                  as n_buyers,
       coalesce(sum(buy_value), 0)                                         as buy_value,
       coalesce(sum(sell_value) filter (where not coalesce(is_10b5_1, false)), 0) as discretionary_sell_value,
       coalesce(sum(sell_value) filter (where is_10b5_1), 0)               as plan_sell_value
from insider_trades
where filed_at >= now() - interval '30 days'
group by ticker;
