"""Storage archive: merging into existing partitions and refusing to overwrite history."""
import io
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from src.storage import archive
from tests.fakes import FakeS3


def test_merges_into_partition_exported_from_postgres():
    """Files written by archive_cold export carry id and timestamp columns."""
    s3 = FakeS3()
    old = pa.table({
        "id": pa.array([7], pa.int64()), "source": ["finnhub"], "ticker": ["AAPL"],
        "accession_no": ["OLD"], "filed_at": pa.array([datetime(2025, 1, 2, tzinfo=timezone.utc)], pa.timestamp("us", tz="UTC")),
        "payload_json": ['{}'],
        "ingested_at": pa.array([datetime(2026, 10, 1, tzinfo=timezone.utc)], pa.timestamp("us", tz="UTC")),
    })
    buf = io.BytesIO()
    pq.write_table(old, buf)
    s3.objects["raw_filings/part=AAPL.parquet"] = buf.getvalue()

    rows = [{"source": "finnhub", "ticker": "AAPL", "accession_no": "NEW",
             "filed_at": datetime(2026, 9, 1, tzinfo=timezone.utc), "payload_json": {"a": 1},
             "ingested_at": datetime.now(timezone.utc)}]
    added, size = archive.upsert_partition("raw_filings", "AAPL", rows,
                                           ("accession_no",), s3=s3)
    assert (added, size) == (1, 2)
    t = archive.read_partition("raw_filings", "AAPL", s3=s3)
    assert t.column("id").to_pylist() == [7, None]


def test_export_refuses_to_overwrite_existing_archive(monkeypatch):
    import pytest

    from src.jobs import archive_cold

    s3 = FakeS3()
    archive.update_manifest("raw_price_bars", {"AAPL": 10}, "ticker", s3=s3)
    monkeypatch.setattr(archive, "s3_client", lambda: s3)
    monkeypatch.setattr(archive, "export_table", lambda *a, **k: pytest.fail("must not export"))
    with pytest.raises(RuntimeError, match="already archived"):
        archive_cold.export(["raw_price_bars"])
