-- 0019_drop_interim.sql — removes objects from interim versions of 0017 that were
-- applied while the portfolio design changed on 2026-10-18 (all empty or derived):
-- investor_trades (replaced by investor_contributions), the paper_summary view
-- (now paper_performance) and paper_nav.initial_mxn (now contributed_mxn).
-- paper_performance selects every column, so it is rebuilt around the drop.
drop view if exists paper_summary;
drop view if exists paper_performance;
drop table if exists investor_trades;
alter table paper_nav drop column if exists initial_mxn;

create or replace view paper_performance as
with f as (select distinct on (portfolio) portfolio, day as first_day from paper_nav order by portfolio, day),
     l as (select distinct on (portfolio) * from paper_nav order by portfolio, day desc)
select l.portfolio, f.first_day, l.day as last_day, l.contributed_mxn, l.nav_mxn, l.liquidation_mxn,
       l.n_positions, l.bench_ticker, l.bench_nav_mxn, l.bench_liquidation_mxn,
       l.twr as return_mxn, l.bench_twr as bench_return_mxn,
       l.liquidation_mxn / l.bench_liquidation_mxn - 1 as edge_after_tax
from l join f using (portfolio);
