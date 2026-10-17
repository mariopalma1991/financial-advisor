"""Monthly visa application ingestion: Finnhub H1-B and Permanent visa data.

Writes to the Storage archive (raw_visa_applications/part={ticker}.parquet), not
Postgres: a year of filings is ~100k rows, which the 0.5 GB database cannot spare.
Idempotent: each ticker's file keeps existing rows on (source, ticker, case_number).

Finnhub visa data is updated quarterly by DOL. Monthly schedule is sufficient.

Rate limit: Finnhub plan allows 30 calls/min → 2.1s between calls.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from src.adapters.finnhub_visa import FinnhubVisaAdapter
from src.config import all_symbols
from src.jobs._symbol_loop import fetch_symbols
from src.quality.run_metadata import job_run
from src.storage import archive, curated

_CALL_DELAY = 2.1  # Finnhub plan: 30 calls/min
# ~1,150 symbols x 2.1 s = ~40 min; stop cleanly before the 60 min workflow timeout.
_BUDGET_S = 50 * 60
_TABLE = "raw_visa_applications"
_KEY = ("source", "ticker", "case_number")


def _record(app, ingested_at: datetime) -> dict:
    """RawVisaApplication -> archive row (same columns as the old Postgres table)."""
    r = asdict(app)
    r["payload_json"] = r.pop("payload", {})
    r["valid_from"] = (
        datetime.combine(app.received_date, datetime.min.time(), tzinfo=timezone.utc)
        if app.received_date
        else ingested_at
    )
    r["ingested_at"] = ingested_at
    return r


def write_archive(apps, s3=None) -> int:
    """Merge applications into one archive file per ticker; returns rows added."""
    s3 = s3 or archive.s3_client()
    now = datetime.now(timezone.utc)
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for a in apps:
        by_ticker[a.ticker].append(_record(a, now))
    added, sizes = 0, {}
    for ticker, rows in by_ticker.items():
        n, size = archive.upsert_partition(_TABLE, ticker, rows, _KEY, s3=s3)
        added += n
        sizes[ticker] = size
    archive.update_manifest(_TABLE, sizes, "ticker", s3=s3)
    return added


def main() -> None:
    with job_run("ingest_visa") as em:
        synced = curated.sync_dim_tickers()
        symbols = all_symbols()

        today = date.today()
        _from = (today - timedelta(days=365)).isoformat()
        to = today.isoformat()

        adapter = FinnhubVisaAdapter()
        # Errors are counted and reported (and a rejected API key fails the
        # job) by the shared loop. No rotation: one run covers the whole universe.
        res = fetch_symbols(
            symbols,
            lambda s: adapter.fetch([s], _from=_from, to=to, strict=True),
            em=em,
            label="finnhub visa",
            delay_s=_CALL_DELAY,
            max_symbols=len(symbols),
            budget_s=_BUDGET_S,
        )
        all_apps = res.items

        em.emit(
            "INFO",
            "source_fetched",
            "finnhub visa",
            payload={"apps": len(all_apps), "symbols": len(res.processed)},
        )

        if all_apps:
            n = write_archive(all_apps)
            em.emit(
                "INFO",
                "rows_written",
                f"storage:{archive.bucket()}/{_TABLE}",
                payload={"rows": n, "dim_tickers_synced": synced},
            )


if __name__ == "__main__":
    main()