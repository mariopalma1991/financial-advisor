from datetime import date

from src.storage import price_history


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[Key])}


def test_history_round_trip_keeps_only_bars_before_the_database():
    s3 = FakeS3()
    rows = [{"ticker": "AAA", "bar_date": date(2024, 9, d), "close": 10.0 + d, "volume": 100,
             "ingested_at": "2026-10-19T00:00:00+00:00"} for d in (19, 16, 17, 18)]
    rows.append({"ticker": "BBB", "bar_date": date(2024, 9, 16), "close": 0.0, "volume": 0,
                 "ingested_at": "2026-10-19T00:00:00+00:00"})
    assert price_history.write(rows, s3=s3) == 5
    got = price_history.load("2024-09-18", s3=s3)
    assert list(got) == ["AAA"]   # BBB's zero close dropped
    assert [b["bar_date"] for b in got["AAA"]] == ["2024-09-16", "2024-09-17"]


def test_history_missing_file_reads_as_empty():
    assert price_history.load("2024-09-18", s3=FakeS3()) == {}
    assert price_history.load(None) == {}
