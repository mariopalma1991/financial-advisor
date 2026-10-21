-- 0023_top1_portfolio.sql — one-stock portfolio (the factor #1) against VOO, in MXN
-- (src/jobs/top1_portfolio.py). Decisions from live_start on are stored once; fills and NAV are
-- recomputed from prices each run. Kept apart from paper_* so the Capa 2 books and views are untouched.

create table if not exists top1_decisions (
    signal_day  date not null,
    ticker      text not null,
    action      text not null,           -- buy | hold | sell
    reason      text,
    rank        int,
    score       real,
    industry    text,
    run_id      text,
    decided_at  timestamptz not null default now(),
    primary key (signal_day, ticker)
);

create table if not exists top1_fills (
    exec_day    date not null,
    signal_day  date not null,
    ticker      text not null,
    side        text not null,           -- buy | sell
    reason      text,
    shares      double precision,
    price_usd   double precision,
    usd_mxn     double precision,
    amount_mxn  double precision,
    cost_mxn    double precision,
    gain_mxn    double precision,
    run_id      text,
    primary key (exec_day, ticker, side)
);

create table if not exists top1_nav (
    day              date primary key,
    ticker           text,               -- the stock held at the close (null: all cash)
    nav_mxn          double precision,
    cash_mxn         double precision,
    liquidation_mxn  double precision,   -- selling everything today: after costs and ISR
    flow_mxn         double precision,
    contributed_mxn  double precision,
    twr              double precision,
    usd_mxn          double precision,
    bench_ticker     text,
    bench_nav_mxn    double precision,
    bench_liquidation_mxn double precision,
    bench_twr        double precision,
    computed_at      timestamptz not null default now(),
    run_id           text
);
