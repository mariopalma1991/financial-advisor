-- 0005_indicators_daily.sql — curated technical indicators (src/jobs/compute_indicators.py).
-- Rebuildable from price_bars_daily, so one row per (ticker, bar_date), overwritten
-- on recompute (no versions). Only the last year is kept (INDICATOR_KEEP_DAYS) to fit
-- the 0.5 GB free database; real (float4) halves the row size, ample for indicators.
-- The unique index serves the per-ticker lookups, so there is no extra index.

drop view if exists current_indicators;
drop table if exists indicators_daily;

create table indicators_daily (
    id               bigint generated always as identity primary key,
    ticker           text not null,
    bar_date         date not null,
    sma_10 real, sma_20 real, sma_50 real, sma_200 real,
    ema_10 real, ema_20 real, ema_50 real, ema_200 real,
    rsi_14 real,
    macd real, macd_signal real, macd_hist real,
    atr_14 real,
    bb_upper real, bb_middle real, bb_lower real,
    volume_ma_20 real,
    rel_strength_21d real,               -- ticker 21d return minus benchmark 21d return
    valid_from       timestamptz not null,
    ingested_at      timestamptz not null default now(),
    run_id           text,
    unique (ticker, bar_date)
);
