"""Move heavy raw history out of Postgres into the Storage archive.

Modes (python -m src.jobs.archive_cold <mode>):
    check   write/read/delete a test object in the bucket and confirm Postgres access
    export  write every row of the archive tables to Parquet (idempotent: overwrites)
    verify  compare each manifest against Postgres row counts
    prune   remove archived rows/payloads from Postgres, then VACUUM FULL.
            Destructive: requires --confirm BORRAR, and only touches rows ingested
            before that table's manifest was written, so nothing unarchived is lost.

Postgres keeps the hot window the feeds read: price bars and filings for the
last HOT_YEARS years, and every news article (its payload feeds news sentiment).
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from src.storage import archive
from src.storage.supabase_client import pg_connection

HOT_YEARS = 3
CONFIRM_WORD = "BORRAR"

# table -> statements run by prune; %(ts)s = manifest exported_at
PRUNE_SQL: dict[str, list[str]] = {
    "raw_price_bars": [
        "delete from raw_price_bars where ingested_at <= %(ts)s",
    ],
    "price_bars_daily": [
        f"delete from price_bars_daily where bar_date < current_date - interval '{HOT_YEARS} years'"
        " and ingested_at <= %(ts)s",
    ],
    "raw_financial_reports": [
        "update raw_financial_reports set report_json = '{}'::jsonb, payload_json = null"
        " where ingested_at <= %(ts)s and (report_json <> '{}'::jsonb or payload_json is not null)",
    ],
    "raw_filings": [
        f"delete from raw_filings where filed_at < now() - interval '{HOT_YEARS} years'"
        " and ingested_at <= %(ts)s",
        "update raw_filings set payload_json = null"
        " where ingested_at <= %(ts)s and payload_json is not null",
    ],
    "raw_visa_applications": [
        "delete from raw_visa_applications where ingested_at <= %(ts)s",
    ],
}


def _db_counts(tables: list[str]) -> dict[str, int]:
    with pg_connection() as conn, conn.cursor() as cur:
        out = {}
        for t in tables:
            cur.execute(f"select count(*) from {t}")
            out[t] = cur.fetchone()[0]
        return out


def _db_size() -> str:
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("select pg_size_pretty(pg_database_size(current_database()))")
        return cur.fetchone()[0]


def check() -> None:
    key = archive.roundtrip_check()
    print(f"storage ok: wrote, read and deleted {archive.bucket()}/{key}")
    print(f"postgres ok: database size {_db_size()}")


def export(tables: list[str]) -> None:
    s3 = archive.s3_client()
    for t in tables:
        m = archive.export_table(t, s3=s3)
        print(
            f"{t}: {m['rows']:,} rows in {len(m['partitions']):,} files,"
            f" {m['bytes'] / 1e6:.1f} MB parquet",
            flush=True,
        )


def verify(tables: list[str]) -> bool:
    s3 = archive.s3_client()
    ok = True
    for t in tables:
        m = archive.read_manifest(t, s3=s3)
        if m is None:
            print(f"{t}: NO MANIFEST")
            ok = False
            continue
        with pg_connection() as conn, conn.cursor() as cur:
            cur.execute(f"select count(*) from {t} where ingested_at <= %s", (m["exported_at"],))
            db_rows = cur.fetchone()[0]
        status = "ok" if m["rows"] >= db_rows else "MISSING ROWS"
        ok &= m["rows"] >= db_rows
        print(f"{t}: archive {m['rows']:,} / db (ingested before export) {db_rows:,} -> {status}")
    return ok


def prune(tables: list[str]) -> None:
    s3 = archive.s3_client()
    manifests: dict[str, Any] = {}
    for t in tables:
        if t not in PRUNE_SQL:
            continue
        m = archive.read_manifest(t, s3=s3)
        if m is None:
            raise RuntimeError(f"{t}: no archive manifest, refusing to prune")
        manifests[t] = m
    if not verify(list(manifests)):
        raise RuntimeError("archive verification failed, refusing to prune")

    print(f"database size before: {_db_size()}")
    with pg_connection() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for t, m in manifests.items():
                for sql in PRUNE_SQL[t]:
                    cur.execute(sql, {"ts": m["exported_at"]})
                    print(f"{t}: {cur.rowcount:,} rows affected", flush=True)
                cur.execute(f"vacuum full analyze {t}")
                print(f"{t}: vacuum full done", flush=True)
    print(f"database size after: {_db_size()}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["check", "export", "verify", "prune"])
    p.add_argument("--tables", default=",".join(archive.ARCHIVE_TABLES))
    p.add_argument("--confirm", default="")
    args = p.parse_args(argv)
    tables = [t for t in args.tables.split(",") if t]
    unknown = set(tables) - set(archive.ARCHIVE_TABLES)
    if unknown:
        p.error(f"unknown tables: {', '.join(sorted(unknown))}")

    if args.mode == "check":
        check()
    elif args.mode == "export":
        export(tables)
    elif args.mode == "verify":
        return 0 if verify(tables) else 1
    elif args.mode == "prune":
        if args.confirm != CONFIRM_WORD:
            p.error(f"prune is destructive: pass --confirm {CONFIRM_WORD}")
        prune(tables)
    return 0


if __name__ == "__main__":
    sys.exit(main())
