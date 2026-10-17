-- 0010_news_sentiment.sql — relevance and sentiment computed at ingest (ingest_news).
--
-- payload_json.about: headline or summary names the company (set from 2026-10-17;
--   older rows fall back to matching the headline against ticker_aliases here).
-- payload_json.sentiment: FinBERT positive - negative in [-1, 1];
--   sentiment_model = finbert (headline + summary) or finbert-headline (backfill).

create or replace view news_relevant as
select distinct on (n.payload_json ->> 'related', lower(n.headline))
       n.id, n.payload_json ->> 'related' as ticker, n.published_at, n.headline,
       n.payload_json ->> 'summary' as summary,   -- always null now: summaries live in the archive
       n.payload_json ->> 'source' as publisher,
       n.url, n.article_id, n.ingested_at,
       (n.payload_json ->> 'sentiment')::real as sentiment,
       n.payload_json ->> 'sentiment_model' as sentiment_model
from raw_news_articles n
where coalesce(
    (n.payload_json ->> 'about')::boolean,
    exists (
        select 1 from ticker_aliases a
        where a.ticker = n.payload_json ->> 'related'
          and case when a.case_sensitive then n.headline ~ a.pattern else n.headline ~* a.pattern end
    )
)
order by n.payload_json ->> 'related', lower(n.headline), n.published_at;

-- Daily sentiment per ticker over relevant articles; day = Mexico City calendar day.
create or replace view news_sentiment_daily as
select ticker,
       (published_at at time zone 'America/Mexico_City')::date as date,
       count(*)                                            as n_articles,
       count(sentiment)                                    as n_scored,
       round(avg(sentiment)::numeric, 3)                   as avg_sentiment,
       count(*) filter (where sentiment >= 0.3)            as n_positive,
       count(*) filter (where sentiment <= -0.3)           as n_negative,
       max(ingested_at)                                    as valid_from
from news_relevant
group by 1, 2;
