-- 0024_order_alerts.sql — the order lists sent to the investor (src/jobs/order_alerts.py):
-- one row per message; a later row for the same book and day is a correction.

create table if not exists order_alerts (
    id          bigserial primary key,
    book        text not null,            -- the book the investor follows (paper_decisions.portfolio)
    exec_day    date not null,            -- the trading day the orders are for (at the close)
    kind        text not null,            -- orders | correction | stale (no prices, no list)
    signature   text not null,            -- the orders' hash: an unchanged list isn't sent again
    orders      jsonb,                    -- fills (side, ticker, shares, price_usd, amount_mxn), vetoed, flow_mxn
    message     text not null,
    sent_at     timestamptz not null default now()
);
create index if not exists order_alerts_book_day on order_alerts (book, exec_day, sent_at desc);
