"""Cold archive: heavy raw history as Parquet in Supabase Storage (S3 API).

The Postgres database is capped at 0.5 GB on the Supabase free plan, while
Storage gets 1 GB. Full history and raw payloads live here; Postgres keeps the
hot window the feeds and agents read.

Layout in the bucket:
    {table}/part={key}.parquet   one file per partition (ticker, or month for news)
    {table}/_manifest.json       row counts per partition + total, written last

Partitions are small on purpose: Storage egress is capped at 5 GB/month, so a
reader fetches only the tickers it needs, never a whole table.
"""
from __future__ import annotations

import io
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

from src.storage.supabase_client import pg_connection

DEFAULT_BUCKET = "market-archive"

# table -> SQL expression used as the partition key. Tables exported from Postgres.
# raw_visa_applications is not here: its ingest writes to the archive directly
# (see upsert_partition), so the archive, not Postgres, is its source of truth.
ARCHIVE_TABLES: dict[str, str] = {
    "raw_price_bars": "ticker",
    "price_bars_daily": "ticker",
    "raw_financial_reports": "ticker",
    "raw_filings": "ticker",
    "raw_news_articles": "to_char(published_at at time zone 'UTC', 'YYYY-MM')",
}


def bucket() -> str:
    return os.getenv("SUPABASE_S3_BUCKET", DEFAULT_BUCKET)


def s3_client():
    missing = [
        k
        for k in (
            "SUPABASE_S3_ENDPOINT",
            "SUPABASE_S3_ACCESS_KEY_ID",
            "SUPABASE_S3_SECRET_ACCESS_KEY",
        )
        if not os.getenv(k)
    ]
    if missing:
        raise RuntimeError(f"Missing Storage S3 settings: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["SUPABASE_S3_ENDPOINT"],
        region_name=os.getenv("SUPABASE_S3_REGION", "ca-central-1"),
        aws_access_key_id=os.environ["SUPABASE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SUPABASE_S3_SECRET_ACCESS_KEY"],
    )


def _safe_key(part: Any) -> str:
    return str(part if part is not None else "_null").replace("/", "_")


def _jsonable(v: Any) -> Any:
    """Normalize a psycopg2 value for Arrow: jsonb -> JSON text, Decimal -> float."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), default=str)
    if isinstance(v, Decimal):
        return float(v)
    return v


def _to_parquet(columns: list[str], rows: list[tuple]) -> bytes:
    data = {c: [_jsonable(r[i]) for r in rows] for i, c in enumerate(columns)}
    buf = io.BytesIO()
    pq.write_table(pa.table(data), buf, compression="zstd")
    return buf.getvalue()


def _partitions(table: str, batch: int = 20_000) -> Iterator[tuple[str, list[str], list[tuple]]]:
    """Stream (partition, columns, rows) from Postgres, one partition at a time."""
    part_expr = ARCHIVE_TABLES[table]
    with pg_connection() as conn:
        with conn.cursor(name=f"archive_{table}") as cur:
            cur.itersize = batch
            cur.execute(
                f"select ({part_expr})::text as _part, t.* from {table} t order by 1, id"
            )
            columns: list[str] | None = None
            current: str | None = None
            rows: list[tuple] = []
            for rec in cur:
                if columns is None:
                    columns = [d[0] for d in cur.description][1:]
                part, row = rec[0], rec[1:]
                if rows and part != current:
                    yield _safe_key(current), columns, rows
                    rows = []
                current = part
                rows.append(row)
            if rows and columns is not None:
                yield _safe_key(current), columns, rows


def export_table(table: str, s3=None) -> dict[str, Any]:
    """Write every row of `table` to Storage; returns the manifest written."""
    s3 = s3 or s3_client()
    counts: dict[str, int] = {}
    nbytes = 0
    for part, columns, rows in _partitions(table):
        body = _to_parquet(columns, rows)
        s3.put_object(Bucket=bucket(), Key=f"{table}/part={part}.parquet", Body=body)
        counts[part] = len(rows)
        nbytes += len(body)
    manifest = {
        "table": table,
        "partition_by": ARCHIVE_TABLES[table],
        "rows": sum(counts.values()),
        "bytes": nbytes,
        "partitions": counts,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=bucket(),
        Key=f"{table}/_manifest.json",
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )
    return manifest


def read_manifest(table: str, s3=None) -> dict[str, Any] | None:
    s3 = s3 or s3_client()
    try:
        obj = s3.get_object(Bucket=bucket(), Key=f"{table}/_manifest.json")
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(obj["Body"].read())


def read_partition(table: str, part: str, s3=None) -> pa.Table:
    """Read one archived partition (e.g. one ticker) back as an Arrow table."""
    s3 = s3 or s3_client()
    obj = s3.get_object(Bucket=bucket(), Key=f"{table}/part={_safe_key(part)}.parquet")
    return pq.read_table(io.BytesIO(obj["Body"].read()))


def upsert_partition(
    table: str, part: str, rows: list[dict[str, Any]], key: tuple[str, ...], s3=None
) -> tuple[int, int]:
    """Merge `rows` into one archived partition, keeping existing rows on key clashes.

    Rows are dicts with Python values (dates, datetimes, dicts for JSON). Returns
    (rows added, partition size). Only that partition is read, to spare egress.
    """
    s3 = s3 or s3_client()
    try:
        existing = read_partition(table, part, s3=s3)
    except s3.exceptions.NoSuchKey:
        existing = None

    new_rows = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    if existing is not None:
        old_rows = existing.to_pylist()
        seen = {tuple(r.get(k) for k in key) for r in old_rows}
        new_rows = [r for r in new_rows if tuple(r.get(k) for k in key) not in seen]
        if not new_rows:
            return 0, len(old_rows)
        merged = pa.concat_tables(
            [existing, pa.Table.from_pylist(new_rows, schema=existing.schema)]
        )
    else:
        merged = pa.Table.from_pylist(new_rows)

    buf = io.BytesIO()
    pq.write_table(merged, buf, compression="zstd")
    s3.put_object(Bucket=bucket(), Key=f"{table}/part={_safe_key(part)}.parquet", Body=buf.getvalue())
    return len(new_rows), merged.num_rows


def update_manifest(table: str, partition_rows: dict[str, int], partition_by: str, s3=None) -> None:
    """Record new partition sizes in a table's manifest (created if missing)."""
    s3 = s3 or s3_client()
    m = read_manifest(table, s3=s3) or {"table": table, "partition_by": partition_by, "partitions": {}}
    m["partitions"].update({_safe_key(p): n for p, n in partition_rows.items()})
    m["rows"] = sum(m["partitions"].values())
    m["exported_at"] = datetime.now(timezone.utc).isoformat()
    s3.put_object(
        Bucket=bucket(),
        Key=f"{table}/_manifest.json",
        Body=json.dumps(m).encode(),
        ContentType="application/json",
    )


def roundtrip_check(s3=None) -> str:
    """Write, read back and delete a small object; returns the key used."""
    s3 = s3 or s3_client()
    key = f"_healthcheck/{date.today().isoformat()}.json"
    payload = json.dumps({"ok": True, "at": datetime.now(timezone.utc).isoformat()}).encode()
    s3.put_object(Bucket=bucket(), Key=key, Body=payload)
    got = s3.get_object(Bucket=bucket(), Key=key)["Body"].read()
    if got != payload:
        raise RuntimeError("Storage round-trip mismatch")
    s3.delete_object(Bucket=bucket(), Key=key)
    return key
