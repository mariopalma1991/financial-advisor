-- 0025_top1_book_tables.sql — the multi-book tables are top1_book_* (portfolio column). top1_decisions,
-- top1_fills and top1_nav stay as views of the desde_enero book, the single-book shape that
-- src/jobs/order_alerts.py and the monitor read, so they never see the mi_inversion book's rows.
alter table top1_decisions rename to top1_book_decisions;
alter table top1_fills rename to top1_book_fills;
alter table top1_nav rename to top1_book_nav;
create view top1_decisions as
  select signal_day, ticker, action, reason, rank, score, industry, run_id, decided_at
  from top1_book_decisions where portfolio = 'desde_enero';
create view top1_fills as
  select exec_day, signal_day, ticker, side, reason, shares, price_usd, usd_mxn, amount_mxn, cost_mxn, gain_mxn, run_id
  from top1_book_fills where portfolio = 'desde_enero';
create view top1_nav as
  select day, ticker, nav_mxn, cash_mxn, liquidation_mxn, flow_mxn, contributed_mxn, twr, usd_mxn, bench_ticker,
         bench_nav_mxn, bench_liquidation_mxn, bench_twr, computed_at, run_id
  from top1_book_nav where portfolio = 'desde_enero';
