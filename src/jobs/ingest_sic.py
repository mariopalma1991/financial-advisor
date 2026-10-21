"""Each tracked company's SEC SIC code (industry) into company_sic, with the peer
group built from it (src/transforms/peers.py). The peer_groups view and the MCP's
get_peer_valuation use these peers instead of Finnhub's when there are enough.
One EDGAR call per company (~1,100 at 10/s); SIC codes rarely change, so monthly.
  python -m src.jobs.ingest_sic
"""
from __future__ import annotations

import psycopg2.extras

from src.adapters.sec_edgar import EdgarClient, parse_sic
from src.config import all_symbols
from src.jobs.ingest_filings import known_ciks, resolve_ciks
from src.quality.run_metadata import current_run_id, job_run
from src.storage.supabase_client import pg_connection
from src.transforms.peers import pick_peers

_COLS = ("ticker", "cik", "sic", "sic_description", "peers", "peer_basis", "run_id")


def main() -> None:
    with job_run("ingest_sic") as em:
        symbols = all_symbols()
        edgar = EdgarClient()
        ciks = resolve_ciks(symbols, edgar.company_tickers(), known_ciks())
        sics: dict[int, tuple[int | None, str | None]] = {}
        failed = 0
        for cik in sorted(set(ciks.values())):
            try:
                sics[cik] = parse_sic(edgar.submissions_json(cik))
            except Exception as e:  # noqa: BLE001 - one company must not stop the run
                failed += 1
                em.emit("WARN", "fetch_failed", f"submissions CIK {cik}", payload={"error": str(e)[:200]})
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute("select ticker, market_cap from current_fundamentals_snapshot")
            caps = dict(cur.fetchall())
            info = {t: (cik, *sics.get(cik, (None, None))) for t, cik in ciks.items()}
            peers = pick_peers({t: (v[1], caps.get(t)) for t, v in info.items()})
            run_id = current_run_id()
            rows = [(t, cik, sic, desc, *(peers.get(t) or (None, None)), run_id)
                    for t, (cik, sic, desc) in info.items()]
            psycopg2.extras.execute_values(
                cur, f"insert into company_sic ({', '.join(_COLS)}) values %s on conflict (ticker) do update set "
                     + ", ".join(f"{c} = excluded.{c}" for c in _COLS[1:]) + ", updated_at = now()",
                rows, page_size=500)
            conn.commit()
        em.emit("INFO", "rows_written", "company_sic", payload={
            "rows": len(rows), "with_sic": sum(1 for r in rows if r[2]), "with_sic_peers": len(peers),
            "no_cik": len(set(symbols) - set(ciks)), "failed": failed})


if __name__ == "__main__":
    main()
