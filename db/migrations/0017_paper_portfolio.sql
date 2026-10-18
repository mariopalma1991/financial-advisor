-- 0017_paper_portfolio.sql — paper portfolio from the factor scores, against VOO, in MXN
-- (src/jobs/paper_portfolio.py, src/transforms/paper.py). Two portfolios:
--   forward_top20   decisions stored once from 2026-10-19 on, never recomputed
--   backtest_top20  the same rules over past month-ends, recomputed every run

create table if not exists paper_decisions (
    portfolio    text not null,
    signal_day   date not null,           -- factor day the decision used
    ticker       text not null,
    action       text not null,           -- buy | hold | sell
    reason       text,                    -- top_rank, rank_within_keep, rank_below_keep, ineligible, ...
    rank         int,
    score        real,
    industry     text,
    run_id       text,
    decided_at   timestamptz not null default now(),
    primary key (portfolio, signal_day, ticker)
);

create table if not exists paper_fills (
    portfolio    text not null,
    exec_day     date not null,           -- next trading day after the signal, at the close
    signal_day   date not null,
    ticker       text not null,
    side         text not null,           -- buy | sell
    reason       text,
    shares       double precision,        -- today's share basis (split-adjusted)
    price_usd    double precision,
    usd_mxn      double precision,
    amount_mxn   double precision,        -- after costs
    cost_mxn     double precision,
    gain_mxn     double precision,        -- sells: realized gain after costs
    run_id       text,
    primary key (portfolio, exec_day, ticker, side)
);

create table if not exists paper_nav (
    portfolio        text not null,
    day              date not null,
    nav_mxn          double precision,    -- cash + positions at the close
    cash_mxn         double precision,
    liquidation_mxn  double precision,    -- selling everything today: after costs and ISR
    n_positions      int,
    initial_mxn      double precision,    -- pesos put in at the start (both books)
    usd_mxn          double precision,
    bench_ticker     text,
    bench_nav_mxn    double precision,    -- the same pesos in VOO since the first fill
    bench_liquidation_mxn double precision,
    run_id           text,
    computed_at      timestamptz not null default now(),
    primary key (portfolio, day)
);

-- One row per portfolio: return since the first fill vs VOO, in MXN.
create or replace view paper_summary as
with f as (select distinct on (portfolio) portfolio, day as first_day from paper_nav order by portfolio, day),
     l as (select distinct on (portfolio) * from paper_nav order by portfolio, day desc)
select l.portfolio, f.first_day, l.day as last_day, l.nav_mxn, l.liquidation_mxn, l.n_positions,
       l.bench_ticker, l.bench_nav_mxn, l.bench_liquidation_mxn,
       l.nav_mxn / l.initial_mxn - 1 as return_mxn,
       l.bench_nav_mxn / l.initial_mxn - 1 as bench_return_mxn,
       l.liquidation_mxn / l.bench_liquidation_mxn - 1 as edge_after_tax
from l join f using (portfolio);
