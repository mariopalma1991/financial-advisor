-- 0024_top1_books.sql — top1_* tables hold several books: desde_enero (retrospective, live from
-- 2026-10-21) and mi_inversion (the investor's own deposits, first buy 2026-10-22).
alter table top1_decisions add column if not exists portfolio text not null default 'desde_enero';
alter table top1_fills add column if not exists portfolio text not null default 'desde_enero';
alter table top1_nav add column if not exists portfolio text not null default 'desde_enero';
alter table top1_decisions drop constraint top1_decisions_pkey, add primary key (portfolio, signal_day, ticker);
alter table top1_fills drop constraint top1_fills_pkey, add primary key (portfolio, exec_day, ticker, side);
alter table top1_nav drop constraint top1_nav_pkey, add primary key (portfolio, day);
