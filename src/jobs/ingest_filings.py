"""Daily filings ingestion: EDGAR XBRL fundamentals + recent filing metadata."""
from __future__ import annotations

from src.adapters.edgar_filings import EdgarAdapter
from src.config import all_symbols
from src.quality.run_metadata import job_run
from src.storage import curated, raw
from src.transforms.fundamentals import to_quarterly_rows


def main() -> None:
    with job_run("ingest_filings") as em:
        symbols = all_symbols()
        edgar = EdgarAdapter()

        facts = edgar.fetch_fundamentals(symbols)
        em.emit("INFO", "source_fetched", "edgar company facts", payload={"facts": len(facts)})

        filings = edgar.fetch_recent_filings(symbols)
        em.emit("INFO", "source_fetched", "edgar recent filings", payload={"rows": len(filings)})

        raw.insert_filings(filings)

        rows = to_quarterly_rows(facts)
        n = curated.upsert_fundamentals(rows)
        em.emit(
            "INFO",
            "rows_written",
            "fundamentals consolidated",
            payload={"quarters_shaped": len(rows), "rows_new": n},
        )


if __name__ == "__main__":
    main()
