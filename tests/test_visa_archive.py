"""Visa ingest writes to the Storage archive: per-ticker merge, no duplicates."""
import io
from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from src.adapters.base import RawVisaApplication
from src.jobs import ingest_visa
from src.storage import archive


class _NoSuchKey(Exception):
    pass


class FakeS3:
    """In-memory stand-in for the boto3 S3 client calls the archive uses."""

    class exceptions:
        NoSuchKey = _NoSuchKey

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def _app(ticker, case, received=date(2026, 9, 1)):
    return RawVisaApplication(
        source="finnhub", ticker=ticker, case_number=case, job_title="Engineer",
        visa_class="H-1B", case_status="Certified", employer_name="ACME",
        begin_date=None, end_date=None, received_date=received, quarter=3, year=2026,
        wage_range_from=100000.0, wage_range_to=None, wage_unit="Year",
        worksite_city="Austin", worksite_state="TX", full_time=True, h1b_dependent=False,
        payload={"soc_code": "15-1252"},
    )


def test_write_archive_merges_and_dedupes():
    s3 = FakeS3()
    assert ingest_visa.write_archive([_app("AAPL", "A1"), _app("MSFT", "M1")], s3=s3) == 2
    # second run: one repeat, one new case
    assert ingest_visa.write_archive([_app("AAPL", "A1"), _app("AAPL", "A2")], s3=s3) == 1

    t = archive.read_partition("raw_visa_applications", "AAPL", s3=s3)
    assert sorted(t.column("case_number").to_pylist()) == ["A1", "A2"]
    assert t.column("payload_json").to_pylist()[0] == '{"soc_code":"15-1252"}'
    m = archive.read_manifest("raw_visa_applications", s3=s3)
    assert m["partitions"] == {"AAPL": 2, "MSFT": 1} and m["rows"] == 3


def test_merges_into_partition_exported_from_postgres():
    """Files written by archive_cold export carry id and timestamp columns."""
    s3 = FakeS3()
    old = pa.table({
        "id": pa.array([7], pa.int64()), "source": ["finnhub"], "ticker": ["AAPL"],
        "case_number": ["OLD"], "received_date": pa.array([date(2025, 1, 2)], pa.date32()),
        "payload_json": ['{}'],
        "ingested_at": pa.array([datetime(2026, 10, 1, tzinfo=timezone.utc)], pa.timestamp("us", tz="UTC")),
    })
    buf = io.BytesIO()
    pq.write_table(old, buf)
    s3.objects["raw_visa_applications/part=AAPL.parquet"] = buf.getvalue()

    rows = [{"source": "finnhub", "ticker": "AAPL", "case_number": "NEW",
             "received_date": date(2026, 9, 1), "payload_json": {"a": 1},
             "ingested_at": datetime.now(timezone.utc)}]
    added, size = archive.upsert_partition("raw_visa_applications", "AAPL", rows,
                                           ("source", "ticker", "case_number"), s3=s3)
    assert (added, size) == (1, 2)
    t = archive.read_partition("raw_visa_applications", "AAPL", s3=s3)
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
