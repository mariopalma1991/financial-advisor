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


def test_history_round_trip_drops_zero_closes():
    s3 = FakeS3()
    rows = [{"ticker": "AAA", "bar_date": date(2024, 9, d), "close": 10.0 + d, "volume": 100,
             "ingested_at": "2026-10-19T00:00:00+00:00"} for d in (19, 16, 17, 18)]
    rows.append({"ticker": "BBB", "bar_date": date(2024, 9, 16), "close": 0.0, "volume": 0,
                 "ingested_at": "2026-10-19T00:00:00+00:00"})
    assert price_history.write(rows, s3=s3) == 5
    got = price_history.load(s3=s3)
    assert list(got) == ["AAA"]   # BBB's zero close dropped
    assert [b["bar_date"] for b in got["AAA"]] == ["2024-09-16", "2024-09-17", "2024-09-18", "2024-09-19"]


def _b(d):
    return {"bar_date": d, "close": 1.0}


def test_merge_fills_only_before_each_tickers_first_database_bar():
    db = {"AAA": [_b("2024-10-07"), _b("2024-10-08")], "CCC": [_b("2024-09-19")]}
    hist = {"AAA": [_b("2024-09-30"), _b("2024-10-04"), _b("2024-10-07")],
            "BBB": [_b("2024-09-30"), _b("2024-12-02")],
            "CCC": [_b("2024-09-18")]}
    price_history.merge(db, hist, "2024-09-30", "2024-11-29")
    assert [b["bar_date"] for b in db["AAA"]] == ["2024-10-04", "2024-10-07", "2024-10-08"]
    assert "BBB" not in db   # 09-30 is not after lo and 12-02 is past hi
    assert [b["bar_date"] for b in db["CCC"]] == ["2024-09-19"]  # older than lo


def test_history_missing_file_reads_as_empty():
    assert price_history.load(s3=FakeS3()) == {}
