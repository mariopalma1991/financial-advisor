-- 0018_committee_log.sql — what the committee (layers 3-4) decided and why, and how
-- each alert did afterwards, in MXN against VOO. The committee runner writes the
-- first three tables; src/jobs/score_alerts.py fills alert_outcomes every day.
-- Heavy traces (full prompts and tool results) go to Storage; trace_path points there.

create table if not exists committee_runs (
    id              bigserial primary key,
    kind            text not null check (kind in ('new_idea', 'holding_review')),
    ticker          text not null,
    owner           text,                  -- holding reviews: whose position
    as_of           timestamptz not null,  -- the MCP as_of every agent read with (replayable)
    started_at      timestamptz not null default now(),
    finished_at     timestamptz,
    status          text not null default 'running',   -- running | done | failed
    model           text,
    prompt_version  text,
    tokens_in       int,
    tokens_out      int,
    cost_usd        real,
    refs_checked    int,                   -- resolve_refs after the run
    refs_mismatched int,
    trace_path      text,                  -- Storage object with the full run
    run_id          text                   -- GitHub run, if run from Actions
);
create index if not exists idx_committee_runs_ticker on committee_runs (ticker, as_of desc);

-- One row per agent per run: its stance, its reasoning and the evidence it cited.
create table if not exists committee_opinions (
    committee_run_id bigint not null references committee_runs (id) on delete cascade,
    role            text not null,         -- optimista, pesimista, contrario, especialista, riesgo_timing, presidente
    stance          text,                  -- buy | sell | hold | veto | no_view
    score           real,                  -- the committee's common scale
    confidence      real,                  -- 0..1
    reasoning       text,                  -- the agent's own summary of why
    evidence        jsonb,                 -- cites ['<_ref>#<field>', ...] with the values quoted
    tool_calls      jsonb,                 -- [{tool, args}] in order
    tokens_in       int,
    tokens_out      int,
    cost_usd        real,
    created_at      timestamptz not null default now(),
    primary key (committee_run_id, role)
);

-- What reaches the investor. Prices are stored as seen at alert time, in USD and MXN.
create table if not exists committee_alerts (
    id               bigserial primary key,
    committee_run_id bigint references committee_runs (id) on delete set null,
    alert_day        date not null,         -- the as_of day of the run
    owner            text not null default 'all',  -- 'all' for new ideas, the investor for reviews
    ticker           text not null,
    alert            text not null check (alert in ('buy', 'sell', 'hold')),
    vetoed           boolean not null default false,  -- the veto blocked a buy (kept to score the veto)
    veto_reason      text,
    conviction       real,
    horizon_months   int,
    price_usd        double precision,
    usd_mxn          double precision,
    price_mxn        double precision,
    target_mxn       double precision,
    stop_mxn         double precision,
    rationale        text,                  -- the chair's reasoning shown to the investor
    created_at       timestamptz not null default now()
);
create index if not exists idx_committee_alerts_ticker on committee_alerts (ticker, alert_day desc);

-- How each alert did: stock vs VOO in pesos, from the close of the next trading day.
create table if not exists alert_outcomes (
    alert_id       bigint not null references committee_alerts (id) on delete cascade,
    horizon_days   int not null,            -- trading days: 21, 63, 126 (1, 3, 6 months)
    entry_day      date not null,
    exit_day       date not null,
    ret_mxn        real,                    -- stock in pesos (dividends not included)
    bench_ret_mxn  real,                    -- VOO in pesos, same days
    excess         real,                    -- ret - bench
    computed_at    timestamptz not null default now(),
    primary key (alert_id, horizon_days)
);

-- Hit rate per alert type: a buy is right when it beats VOO, a sell when it trails it,
-- a hold when it does not trail it. Vetoed buys show what the veto saved (or cost).
create or replace view alert_scorecard as
select a.alert, a.vetoed, o.horizon_days, count(*) as n,
       avg(o.excess) as avg_excess,
       avg(case when a.alert = 'sell' then (o.excess < 0)::int else (o.excess >= 0)::int end) as hit_rate
from alert_outcomes o join committee_alerts a on a.id = o.alert_id
where o.excess is not null
group by a.alert, a.vetoed, o.horizon_days;

-- The same per agent: was each role's stance right on the alerts it took part in.
create or replace view agent_scorecard as
select p.role, o.horizon_days, count(*) as n,
       avg(case p.stance when 'buy' then (o.excess >= 0)::int
                         when 'sell' then (o.excess < 0)::int
                         when 'veto' then (o.excess < 0)::int
                         when 'hold' then (o.excess >= 0)::int end) as hit_rate,
       avg(p.confidence) as avg_confidence
from committee_opinions p
join committee_alerts a on a.committee_run_id = p.committee_run_id
join alert_outcomes o on o.alert_id = a.id
where o.excess is not null and p.stance in ('buy', 'sell', 'hold', 'veto')
group by p.role, o.horizon_days;
