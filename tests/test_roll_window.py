"""archive_cold roll: rows outside the hot window go to the archive, then leave Postgres."""
import io
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from src.jobs import archive_cold
from src.storage import archive
from tests.fakes import FakeS3


def _news(i, month):
    return {"id": i, "source": "finnhub", "article_id": f"fh_{i}", "headline": f"h{i}",
            "published_at": datetime(2025, month, 3, tzinfo=timezone.utc),
            "payload_json": {"related": "AAPL"}, "run_id": "123",
            "ingested_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}


def test_roll_archives_then_deletes_exact_ids(monkeypatch):
    s3 = FakeS3()
    # an archive file written by the first export: no run_id column, already holds id 1
    old = pa.table({"id": pa.array([1], pa.int64()), "source": ["finnhub"], "article_id": ["fh_1"],
                    "headline": ["h1"],
                    "published_at": pa.array([datetime(2025, 3, 3, tzinfo=timezone.utc)], pa.timestamp("us", tz="UTC")),
                    "payload_json": ['{}'],
                    "ingested_at": pa.array([datetime(2026, 10, 1, tzinfo=timezone.utc)], pa.timestamp("us", tz="UTC"))})
    buf = io.BytesIO()
    pq.write_table(old, buf)
    s3.objects["raw_news_articles/part=2025-03.parquet"] = buf.getvalue()
    archive.update_manifest("raw_news_articles", {"2025-03": 1}, "month", s3=s3)

    calls = []
    monkeypatch.setattr(archive_cold, "_cold_rows",
                        lambda t: iter([("2025-03", [_news(1, 3), _news(2, 3)]), ("2025-04", [_news(3, 4)])]))
    monkeypatch.setattr(archive_cold, "_delete_ids", lambda t, ids, **_: calls.append((t, ids)) or len(ids))
    monkeypatch.setattr(archive_cold, "_db_size", lambda: "300 MB")

    out = archive_cold.roll(["raw_news_articles"], s3=s3)

    assert out == {"raw_news_articles": {"archived": 2, "deleted": 3}}
    assert calls == [("raw_news_articles", [1, 2, 3])]
    t = archive.read_partition("raw_news_articles", "2025-03", s3=s3)
    assert t.column("id").to_pylist() == [1, 2]
    assert t.column("run_id").to_pylist() == [None, "123"]  # column added after export
    m = archive.read_manifest("raw_news_articles", s3=s3)
    assert m["partitions"] == {"2025-03": 2, "2025-04": 1} and m["rows"] == 3


def test_roll_deletes_nothing_when_window_is_empty(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(archive_cold, "_cold_rows", lambda t: iter([]))
    monkeypatch.setattr(archive_cold, "_delete_ids", lambda t, ids, **_: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(archive_cold, "_db_size", lambda: "300 MB")
    assert archive_cold.roll(["price_bars_daily"], s3=s3) == {"price_bars_daily": {"archived": 0, "deleted": 0}}
    assert archive.read_manifest("price_bars_daily", s3=s3) is None


def test_roll_windows_cover_only_archived_tables():
    assert set(archive_cold.ROLL_WHERE) <= set(archive.ARCHIVE_TABLES)
    assert "90 days" in archive_cold.ROLL_WHERE["raw_news_articles"]
    assert "'144'" in archive_cold.ROLL_WHERE["raw_filings"]
    assert set(archive_cold.BEFORE_DELETE) <= set(archive_cold.ROLL_WHERE)
