-- 0012_news_sentiment_history.sql — keep daily news sentiment after articles leave Postgres.
--
-- roll_window moves articles older than NEWS_HOT_DAYS (90) to the archive. Before
-- deleting them it copies their per-day aggregates here, so news_sentiment_daily
-- keeps its full history while raw_news_articles holds only the hot window.

create table if not exists news_sentiment_history (
    ticker        text   not null,
    date          date   not null,
    n_articles    bigint not null,
    n_scored      bigint not null,
    avg_sentiment numeric,
    n_positive    bigint not null,
    n_negative    bigint not null,
    valid_from    timestamptz,
    primary key (ticker, date)
);

-- Aggregates over the articles still in Postgres (the former news_sentiment_daily).
create or replace view news_sentiment_live as
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

-- Live days plus archived days before the oldest article still in Postgres.
create or replace view news_sentiment_daily as
select * from news_sentiment_live
union all
select h.* from news_sentiment_history h
where h.date < coalesce(
    (select (min(published_at) at time zone 'America/Mexico_City')::date from raw_news_articles),
    'infinity'::date);
