"""Price history older than the database window, as one Parquet file in the cold archive.

price_bars_daily keeps two years (roll_window). The factor backtest needs a year
more (12-month momentum before its first month-end) and keeps needing the
backtest's early months after roll_window trims them, so the closes and volumes
from Yahoo live at price_history/bars.parquet (all tickers, one file: a few MB
read once per run, to spare Storage egress).

Readers use these rows only before the database's first bar, so the database
stays the source wherever it has data. Without Storage settings the readers get
nothing and work from the database alone.
"""
from __future__ import annotations

import io
import logging
import os
from collections import defaultdict
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from src.storage import archive

KEY = "price_history/bars.parquet"
COLUMNS = ("ticker", "bar_date", "close", "volume", "ingested_at")
log = logging.getLogger(__name__)


def write(rows: list[dict], s3=None) -> int:
    """Replace the history file with rows (dicts with COLUMNS)."""
    s3 = s3 or archive.s3_client()
    table = pa.Table.from_pylist([{c: r.get(c) for c in COLUMNS} for r in rows])
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    s3.put_object(Bucket=archive.bucket(), Key=KEY, Body=buf.getvalue())
    return table.num_rows


def load(before: date | str | None, s3=None) -> dict[str, list[dict]]:
    """Bars per ticker (ascending) dated before `before` (the database's first bar).
    Empty when Storage isn't configured or the file doesn't exist yet."""
    if before is None:
        return {}
    if s3 is None and not os.getenv("SUPABASE_S3_ENDPOINT"):
        log.info("price_history: no Storage settings, database only")
        return {}
    s3 = s3 or archive.s3_client()
    try:
        obj = s3.get_object(Bucket=archive.bucket(), Key=KEY)
    except Exception as e:  # noqa: BLE001 -- missing file or Storage down: the database alone still works
        log.warning("price_history: not read (%s)", e)
        return {}
    cut = str(before)[:10]
    out: dict[str, list[dict]] = defaultdict(list)
    for r in pq.read_table(io.BytesIO(obj["Body"].read())).to_pylist():
        d = str(r["bar_date"])[:10]
        if d < cut and r["close"] and r["close"] > 0:
            out[r["ticker"]].append({"bar_date": d, "close": float(r["close"]),
                                     "volume": float(r["volume"] or 0),
                                     "ingested_at": str(r["ingested_at"])})
    for b in out.values():
        b.sort(key=lambda x: x["bar_date"])
    return dict(out)
