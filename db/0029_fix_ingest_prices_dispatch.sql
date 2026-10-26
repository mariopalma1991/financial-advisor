-- dispatch_ingest_prices called dispatch_workflow('ingest_prices', null): the untyped null picked the
-- old (text, text) overload, whose ref was null, so the 2026-10-24 02:00 UTC run failed and the
-- Friday close was never ingested (no Monday alert). Type the null so the jsonb overload is used.
select cron.alter_job(
  (select jobid from cron.job where jobname = 'dispatch_ingest_prices'),
  command := $$select public.dispatch_workflow('ingest_prices', NULL::jsonb)$$);
