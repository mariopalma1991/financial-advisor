-- Moves every scheduled GitHub workflow to Supabase pg_cron (public.dispatch_workflow, see 0026).
-- Same cron expressions as the removed `schedule:` blocks, all in UTC. order_alerts and
-- ingest_prices were moved earlier (0026).
select cron.schedule(j.job, j.expr,
  format($c$select public.dispatch_workflow(%L, %L::jsonb)$c$, j.workflow, j.inputs))
from (values
  ('dispatch_committee_nightly',  'committee',          '15 4,12 * * *',    '{"scheduled":"true"}'),
  ('dispatch_committee_weekly',   'committee',          '30 4 * * 2',       '{"scheduled":"true","weekly":"true"}'),
  ('dispatch_enrich_filings',     'enrich_filings',     '0 15 * * 1-6',     null),
  ('dispatch_ingest_analysts',    'ingest_analysts',    '0 16 * * 3',       null),
  ('dispatch_ingest_earncal',     'ingest_earncal',     '0 18 * * 1',       null),
  ('dispatch_ingest_earnings',    'ingest_earnings',    '0 17 * * 1',       null),
  ('dispatch_ingest_filings',     'ingest_filings',     '0 14 * * 1-6',     null),
  ('dispatch_ingest_finra_short', 'ingest_finra_short', '0 16 * * 3',       null),
  ('dispatch_ingest_fundamentals','ingest_fundamentals','0 16 * * 2',       null),
  ('dispatch_ingest_insider_sentiment','ingest_insider_sentiment','0 12 * * 0', null),
  ('dispatch_ingest_macro',       'ingest_macro',       '0 3 * * 2-6',      null),
  ('dispatch_ingest_news',        'ingest_news',        '0 13,19,23 * * *', null),
  ('dispatch_ingest_profiles',    'ingest_profiles',    '0 16 * * 4',       null),
  ('dispatch_ingest_sic',         'ingest_sic',         '0 15 2 * *',       null),
  ('dispatch_ingest_street',      'ingest_street',      '0 17 * * 6',       null),
  ('dispatch_monitor_health',     'monitor_health',     '30 15 * * *',      null),
  ('dispatch_paper_portfolio',    'paper_portfolio',    '*/10 * * * *',     '{"check_deposits":"true"}'),
  ('dispatch_roll_window',        'roll_window',        '0 9 2 * *',        null)
) as j(job, workflow, expr, inputs);

-- keep the dispatch log small (paper_portfolio fires every 10 minutes)
select cron.schedule('prune_workflow_dispatch_log', '17 7 * * *',
  $c$delete from public.workflow_dispatch_log where requested_at < now() - interval '14 days'$c$);
