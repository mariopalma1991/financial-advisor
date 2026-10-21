-- 0022_sic_peers.sql — industry (SEC SIC code) and peer groups built from it.
-- Finnhub's /stock/peers puts companies in broad buckets: Oscar Health (a health
-- insurer, SIC 6324) came back with MetLife, Aflac and other life insurers (6311).
-- src/jobs/ingest_sic.py reads each company's SIC code from EDGAR and picks up to 12
-- peers with the same 4-digit code (3-digit when fewer than 4 share it), closest in
-- market cap first. Finnhub's list stays as the fallback when neither level has 4.
create table if not exists company_sic (
    ticker           text primary key,
    cik              integer,
    sic              integer,
    sic_description  text,
    peers            text[],          -- null when the SIC code gives fewer than 4 peers
    peer_basis       text,            -- 'sic4' or 'sic3'; null = use Finnhub's peers
    updated_at       timestamptz not null default now(),
    run_id           text
);

-- The peers each ticker is compared with, and where the list came from.
create or replace view peer_groups as
select cp.ticker,
       coalesce(cs.peers, cp.peers) as peers,
       coalesce(cs.peer_basis, case when cp.peers is not null then 'finnhub' end) as peer_basis,
       cs.sic, cs.sic_description
from company_profiles cp
left join company_sic cs using (ticker);

create or replace view peer_comparison as
with p as (
    select g.ticker, unnest(g.peers) as peer from peer_groups g
),
med as (
    select p.ticker, count(*) as n_peers,
           percentile_cont(0.5) within group (order by s.pe_ttm) filter (where s.pe_ttm > 0) as peer_pe_ttm,
           percentile_cont(0.5) within group (order by s.ps_ttm)               as peer_ps_ttm,
           percentile_cont(0.5) within group (order by s.pb)                   as peer_pb,
           percentile_cont(0.5) within group (order by s.gross_margin_ttm)     as peer_gross_margin_ttm,
           percentile_cont(0.5) within group (order by s.operating_margin_ttm) as peer_operating_margin_ttm,
           percentile_cont(0.5) within group (order by s.revenue_growth_ttm_yoy) as peer_revenue_growth_ttm_yoy,
           percentile_cont(0.5) within group (order by s.roe_ttm)              as peer_roe_ttm
    from p join current_fundamentals_snapshot s on s.ticker = p.peer
    group by p.ticker
)
select cp.ticker, cp.industry, m.n_peers,
       s.pe_ttm, m.peer_pe_ttm, s.ps_ttm, m.peer_ps_ttm, s.pb, m.peer_pb,
       s.gross_margin_ttm, m.peer_gross_margin_ttm,
       s.operating_margin_ttm, m.peer_operating_margin_ttm,
       s.revenue_growth_ttm_yoy, m.peer_revenue_growth_ttm_yoy,
       s.roe_ttm, m.peer_roe_ttm
from company_profiles cp
left join med m using (ticker)
left join current_fundamentals_snapshot s using (ticker);

-- Same as 0015, peers now from peer_groups (today's list; metrics at p_at).
create or replace function peer_comparison_at(p_ticker text, p_at timestamptz)
returns table (
    ticker text, snapshot_as_of date, industry text, n_peers bigint,
    pe_ttm real, peer_pe_ttm double precision, ps_ttm real, peer_ps_ttm double precision,
    pb real, peer_pb double precision, gross_margin_ttm real, peer_gross_margin_ttm double precision,
    operating_margin_ttm real, peer_operating_margin_ttm double precision,
    revenue_growth_ttm_yoy real, peer_revenue_growth_ttm_yoy double precision,
    roe_ttm real, peer_roe_ttm double precision
) language sql stable as $$
with snap as (
    select distinct on (s.ticker) s.* from fundamentals_snapshot s
    where s.as_of <= p_at::date
      and s.ticker in (select p_ticker union select unnest(peers) from peer_groups where ticker = p_ticker)
    order by s.ticker, s.as_of desc
),
med as (
    select count(*) as n_peers,
           percentile_cont(0.5) within group (order by s.pe_ttm) filter (where s.pe_ttm > 0) as peer_pe_ttm,
           percentile_cont(0.5) within group (order by s.ps_ttm)               as peer_ps_ttm,
           percentile_cont(0.5) within group (order by s.pb)                   as peer_pb,
           percentile_cont(0.5) within group (order by s.gross_margin_ttm)     as peer_gross_margin_ttm,
           percentile_cont(0.5) within group (order by s.operating_margin_ttm) as peer_operating_margin_ttm,
           percentile_cont(0.5) within group (order by s.revenue_growth_ttm_yoy) as peer_revenue_growth_ttm_yoy,
           percentile_cont(0.5) within group (order by s.roe_ttm)              as peer_roe_ttm
    from snap s where s.ticker <> p_ticker
)
select cp.ticker, me.as_of, cp.industry, m.n_peers,
       me.pe_ttm, m.peer_pe_ttm, me.ps_ttm, m.peer_ps_ttm, me.pb, m.peer_pb,
       me.gross_margin_ttm, m.peer_gross_margin_ttm, me.operating_margin_ttm, m.peer_operating_margin_ttm,
       me.revenue_growth_ttm_yoy, m.peer_revenue_growth_ttm_yoy, me.roe_ttm, m.peer_roe_ttm
from company_profiles cp
cross join med m
left join snap me on me.ticker = cp.ticker
where cp.ticker = p_ticker
$$;
