-- 0019_drop_interim.sql — removes objects from interim versions of 0017 that were
-- applied while the portfolio design changed on 2026-10-18 (all empty or derived):
-- investor_trades (replaced by investor_contributions), the paper_summary view
-- (now paper_performance) and paper_nav.initial_mxn (now contributed_mxn).
drop view if exists paper_summary;
drop table if exists investor_trades;
alter table paper_nav drop column if exists initial_mxn;
