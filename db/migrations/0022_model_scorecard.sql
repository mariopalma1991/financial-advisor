-- Capa 3 model test: how each model's committee did, per scoring horizon (1 week, 1, 3 and
-- 6 months). An approval is right when the stock beat VOO in MXN, a veto when it lagged;
-- edge = mean excess of approved names minus mean excess of vetoed ones (a good veto
-- filter has a positive edge). Every run counts, nightly new buys and the weekly test.
create or replace view model_scorecard as
select r.model, o.horizon_days, count(*) as n,
       count(*) filter (where a.vetoed) as vetoes,
       avg(case when a.vetoed then (o.excess < 0)::int else (o.excess >= 0)::int end) as hit_rate,
       avg(o.excess) filter (where not a.vetoed) as approved_excess,
       avg(o.excess) filter (where a.vetoed) as vetoed_excess,
       avg(o.excess) filter (where not a.vetoed) - avg(o.excess) filter (where a.vetoed) as edge,
       sum(r.cost_usd) as cost_usd
from alert_outcomes o
join committee_alerts a on a.id = o.alert_id
join committee_runs r on r.id = a.committee_run_id
where o.excess is not null
group by r.model, o.horizon_days;
