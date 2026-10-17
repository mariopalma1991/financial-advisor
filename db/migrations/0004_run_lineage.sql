-- 0004_run_lineage.sql — row-level lineage and a per-run status view.
-- Every row an ingest writes carries the run_id of the job run that wrote it
-- (GITHUB_RUN_ID in CI, so it links straight to the Actions log; a local id
-- otherwise). Rows written before this migration have run_id null.

alter table raw_price_bars          add column if not exists run_id text;
alter table raw_corporate_actions   add column if not exists run_id text;
alter table raw_news_articles       add column if not exists run_id text;
alter table raw_filings             add column if not exists run_id text;
alter table raw_financial_reports   add column if not exists run_id text;
alter table raw_earnings_surprises  add column if not exists run_id text;
alter table raw_earnings_calendar   add column if not exists run_id text;
alter table raw_visa_applications   add column if not exists run_id text;
alter table raw_insider_sentiment   add column if not exists run_id text;
alter table price_bars_daily        add column if not exists run_id text;

-- One row per job run, rebuilt from pipeline_events.
-- status: failed (job_failed), completed / completed_with_warnings (job_completed),
-- running (no end event yet), abandoned (no end event and silent for 3 hours: the
-- runner was killed, usually by the workflow timeout, before it could log the end).
create or replace view job_runs as
select
    e.run_id,
    e.job_name,
    min(e.created_at)                                                   as started_at,
    max(e.created_at) filter (where e.event_type in ('job_completed', 'job_failed')) as ended_at,
    case
        when bool_or(e.event_type = 'job_failed') then 'failed'
        when bool_or(e.event_type = 'job_completed' and e.level = 'WARN') then 'completed_with_warnings'
        when bool_or(e.event_type = 'job_completed') then 'completed'
        when max(e.created_at) < now() - interval '3 hours' then 'abandoned'
        else 'running'
    end                                                                 as status,
    max((e.payload_json ->> 'duration_s')::numeric)
        filter (where e.event_type in ('job_completed', 'job_failed')) as duration_s,
    coalesce(sum((e.payload_json ->> 'rows')::bigint)
        filter (where e.event_type = 'rows_written' and e.payload_json ? 'rows'), 0) as rows_written,
    count(*) filter (where e.level = 'WARN')                           as warnings,
    count(*) filter (where e.level = 'ERROR')                          as errors,
    max(e.message) filter (where e.event_type = 'job_failed')          as error,
    case when e.run_id ~ '^[0-9]+$'
        then 'https://github.com/mariopalma1991/financial-advisor/actions/runs/' || e.run_id
    end                                                                 as log_url
from pipeline_events e
group by e.run_id, e.job_name;
