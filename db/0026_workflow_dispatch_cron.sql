-- Externally timed GitHub workflow runs: pg_cron -> pg_net -> workflow_dispatch API.
-- GitHub's own cron is delayed by hours; this fires on time. Token: Vault secret GITHUB_ACCESS_TOKEN.
create extension if not exists pg_net;
create extension if not exists pg_cron;

create table if not exists public.workflow_dispatch_log (
  id bigserial primary key,
  workflow text not null,
  ref text not null default 'main',
  requested_at timestamptz not null default now(),
  request_id bigint
);

create or replace function public.dispatch_workflow(p_workflow text, p_inputs jsonb, p_ref text default 'main')
returns bigint
language plpgsql
security definer
set search_path = public, extensions, vault
as $fn$
declare
  v_token text;
  v_req bigint;
  v_body jsonb := jsonb_build_object('ref', p_ref);
begin
  select decrypted_secret into v_token from vault.decrypted_secrets where name = 'GITHUB_ACCESS_TOKEN' limit 1;
  if v_token is null then
    raise exception 'GITHUB_ACCESS_TOKEN not found in vault';
  end if;
  if p_inputs is not null then
    v_body := v_body || jsonb_build_object('inputs', p_inputs);
  end if;
  select net.http_post(
    url := 'https://api.github.com/repos/mariopalma1991/financial-advisor/actions/workflows/' || p_workflow || '.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || v_token,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'finadv-pg-cron',
      'Content-Type', 'application/json'),
    body := v_body
  ) into v_req;
  insert into public.workflow_dispatch_log(workflow, ref, request_id) values (p_workflow, p_ref, v_req);
  return v_req;
end;
$fn$;

revoke all on function public.dispatch_workflow(text, jsonb, text) from public, anon, authenticated;

create or replace view public.workflow_dispatch_status as
select l.id, l.workflow, l.requested_at, l.request_id, r.status_code, left(r.content, 300) as response
from public.workflow_dispatch_log l
left join net._http_response r on r.id = l.request_id
order by l.id desc;

-- 08:00 CST Mon-Fri. order_alerts keeps GitHub's schedule as backup (the job sends once per day).
select cron.schedule('dispatch_order_alerts', '0 14 * * 1-5',
  $c$select public.dispatch_workflow('order_alerts', '{"dry_run":"false","test":"false"}'::jsonb)$c$);

-- 20:00 CST Mon-Fri. Scheduled only after ingest_prices.yml lost its own `schedule:` (same PR):
-- select cron.schedule('dispatch_ingest_prices', '0 2 * * 2-6',
--   $c$select public.dispatch_workflow('ingest_prices', null)$c$);
