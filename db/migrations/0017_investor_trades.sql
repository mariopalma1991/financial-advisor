-- 0017_investor_trades.sql — the investor's own buys and sells, in MXN. The investor
-- rebalances; the committee reads this (MCP get_portfolio) to say hold or sell.
-- Recorded from chat: the investor tells Claude "compré 10 MSFT a 8,500 pesos".

create table if not exists investor_trades (
    id           bigserial primary key,
    owner        text not null default 'mario',
    ticker       text not null,
    side         text not null check (side in ('buy', 'sell')),
    trade_date   date not null,
    shares       double precision not null check (shares > 0),  -- as traded (later splits adjusted on read)
    price_mxn    double precision not null check (price_mxn > 0),  -- per share, in pesos
    fees_mxn     double precision not null default 0 check (fees_mxn >= 0),  -- commission + IVA, if known
    note         text,
    source       text not null default 'chat',
    recorded_at  timestamptz not null default now()
);
create index if not exists idx_investor_trades_owner on investor_trades (owner, trade_date);
