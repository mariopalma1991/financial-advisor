"""Move heavy raw history out of Postgres into the Storage archive.

Modes (python -m src.jobs.archive_cold <mode>):
    check   write/read/delete a test object in the bucket and confirm Postgres access
    export  write every row of the archive tables to Parquet. Refuses a table that
            already has a manifest unless --force: once pruned, Postgres holds less
            than the archive, and re-exporting would overwrite the history.
    verify  compare each manifest against Postgres row counts
    prune   remove archived rows/payloads from Postgres, then VACUUM FULL.
            Destructive: requires --confirm BORRAR, and only touches rows ingested
            before that table's manifest was written, so nothing unarchived is lost.
    roll    monthly rolling window (workflow roll_window): merge rows that left the
            hot window into the archive, then delete exactly those ids from Postgres
            and VACUUM so new rows reuse the space. Rows are deleted only after their
            partition file and the manifest are written.

Postgres keeps the hot window the feeds read: price bars and filings for the
last HOT_YEARS years, Form 4/144 for INSIDER_HOT_DAYS, and news for the last
NEWS_HOT_DAYS days (whole Mexico City days; their daily sentiment is kept in
news_sentiment_history).
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Iterator

from src.quality.run_metadata import job_run
from src.storage import archive
from src.storage.supabase_client import pg_connection

HOT_YEARS = 2  # Mario chose 2 (was 3) on 2026-10-17 to keep the DB under ~70%
# Mario chose 90 days of news and 1 year of Form 4/144 on 2026-10-17 (DB at 474/500 MB)
NEWS_HOT_DAYS = 90  # feeds read 30 days of news
INSIDER_HOT_DAYS = 365  # insider_trades is enriched for the last 90 days only
INSIDER_FORMS = "('4', '4/A', '144', '144/A')"
_MX = "'America/Mexico_City'"
NEWS_CUTOFF = f"(date_trunc('day', now() at time zone {_MX}) - interval '{NEWS_HOT_DAYS} days') at time zone {_MX}"
CONFIRM_WORD = "BORRAR"

# table -> rows outside the hot window, moved to the archive by `roll`
ROLL_WHERE: dict[str, str] = {
    "raw_news_articles": f"published_at < {NEWS_CUTOFF}",
    "price_bars_daily": f"bar_date < current_date - interval '{HOT_YEARS} years'",
    "raw_filings": f"filed_at < now() - interval '{HOT_YEARS} years' or (form_type in {INSIDER_FORMS}"
                   f" and filed_at < now() - interval '{INSIDER_HOT_DAYS} days')",
}
# table -> statement run right before its rows are deleted
BEFORE_DELETE: dict[str, str] = {
    "raw_news_articles": (
        "insert into news_sentiment_history select * from news_sentiment_live"
        f" where date < (now() at time zone {_MX})::date - {NEWS_HOT_DAYS}"
        " on conflict (ticker, date) do update set n_articles = excluded.n_articles,"
        " n_scored = excluded.n_scored, avg_sentiment = excluded.avg_sentiment,"
        " n_positive = excluded.n_positive, n_negative = excluded.n_negative,"
        " valid_from = excluded.valid_from"
    ),
}
DELETE_BATCH = 5_000

# table -> statements run by prune; %(ts)s = manifest exported_at
PRUNE_SQL: dict[str, list[str]] = {
    "raw_price_bars": [
        "delete from raw_price_bars where ingested_at <= %(ts)s",
    ],
    "price_bars_daily": [
        f"delete from price_bars_daily where bar_date < current_date - interval '{HOT_YEARS} years'"
        " and ingested_at <= %(ts)s",
    ],
    "raw_filings": [
        f"delete from raw_filings where filed_at < now() - interval '{HOT_YEARS} years'"
        " and ingested_at <= %(ts)s",
        "update raw_filings set payload_json = null"
        " where ingested_at <= %(ts)s and payload_json is not null",
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


def export(tables: list[str], force: bool = False) -> None:
    s3 = archive.s3_client()
    if not force:
        existing = [t for t in tables if archive.read_manifest(t, s3=s3) is not None]
        if existing:
            raise RuntimeError(
                f"already archived: {', '.join(existing)}. Re-exporting would overwrite "
                "history pruned from Postgres; pass --force only if Postgres is complete."
            )
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


def _table_bytes(table: str) -> int:
    with pg_connection() as conn, conn.cursor() as cur:
        cur.execute("select pg_total_relation_size(%s::regclass)", (table,))
        return cur.fetchone()[0]


def _cold_rows(table: str) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Yield (partition, rows) for the rows of `table` outside its hot window."""
    part_expr = archive.ARCHIVE_TABLES[table]
    with pg_connection() as conn, conn.cursor(name=f"roll_{table}") as cur:
        cur.itersize = 20_000
        cur.execute(
            f"select ({part_expr})::text as _part, t.* from {table} t"
            f" where {ROLL_WHERE[table]} order by 1, id"
        )
        cols: list[str] | None = None
        current, rows = None, []
        for rec in cur:
            if cols is None:
                cols = [d[0] for d in cur.description][1:]
            if rows and rec[0] != current:
                yield current, rows
                rows = []
            current = rec[0]
            rows.append(dict(zip(cols, rec[1:])))
        if rows:
            yield current, rows


def _delete_ids(table: str, ids: list[int], vacuum_full: bool = False) -> int:
    deleted = 0
    with pg_connection() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            if table in BEFORE_DELETE:
                cur.execute(BEFORE_DELETE[table])
            for i in range(0, len(ids), DELETE_BATCH):
                cur.execute(f"delete from {table} where id = any(%s)", (ids[i:i + DELETE_BATCH],))
                deleted += cur.rowcount
            # Plain VACUUM lets new rows reuse the space; FULL rewrites the table and
            # returns it to the plan quota, but needs room for a full copy meanwhile.
            cur.execute(f"vacuum {'full ' if vacuum_full else ''}analyze {table}")
    return deleted


def roll(tables: list[str], s3=None, vacuum_full: bool = False) -> dict[str, dict[str, int]]:
    """Move rows that left the hot window to the archive, then delete them.

    vacuum_full: compact each table after deleting (one-off, e.g. after shrinking
    the window). Tables are processed smallest first so the temporary copy that
    VACUUM FULL needs fits while the database is near its cap.
    """
    s3 = s3 or archive.s3_client()
    out = {}
    if vacuum_full:
        tables = sorted(tables, key=_table_bytes)
    for t in tables:
        if t not in ROLL_WHERE:
            continue
        sizes: dict[str, int] = {}
        ids: list[int] = []
        added = 0
        for part, rows in _cold_rows(t):
            n, size = archive.upsert_partition(t, part, rows, ("id",), s3=s3)
            added += n
            sizes[part] = size
            ids.extend(r["id"] for r in rows)
        if not ids:
            print(f"{t}: nothing outside the hot window", flush=True)
            out[t] = {"archived": 0, "deleted": 0}
            continue
        # Manifest first: a crash after it leaves rows in both places, never in neither.
        archive.update_manifest(t, sizes, archive.ARCHIVE_TABLES[t], s3=s3)
        deleted = _delete_ids(t, ids, vacuum_full=vacuum_full)
        print(f"{t}: {added:,} rows added to {len(sizes):,} archive files,"
              f" {deleted:,} deleted from Postgres", flush=True)
        out[t] = {"archived": added, "deleted": deleted}
    print(f"database size: {_db_size()}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["check", "export", "verify", "prune", "roll"])
    p.add_argument("--tables", default=",".join(archive.ARCHIVE_TABLES))
    p.add_argument("--confirm", default="")
    p.add_argument("--force", action="store_true", help="export: overwrite existing archives")
    p.add_argument("--vacuum-full", action="store_true", help="roll: compact tables after deleting")
    args = p.parse_args(argv)
    tables = [t for t in args.tables.split(",") if t]
    unknown = set(tables) - set(archive.ARCHIVE_TABLES)
    if unknown:
        p.error(f"unknown tables: {', '.join(sorted(unknown))}")

    if args.mode == "check":
        check()
    elif args.mode == "export":
        export(tables, force=args.force)
    elif args.mode == "verify":
        return 0 if verify(tables) else 1
    elif args.mode == "prune":
        if args.confirm != CONFIRM_WORD:
            p.error(f"prune is destructive: pass --confirm {CONFIRM_WORD}")
        prune(tables)
    elif args.mode == "roll":
        with job_run("roll_window") as em:
            for t, n in roll(tables, vacuum_full=args.vacuum_full).items():
                em.emit("INFO", "rows_archived", f"{t}: {n['deleted']:,} rows moved to the archive",
                        payload={"table": t, **n})
    return 0


if __name__ == "__main__":
    sys.exit(main())
