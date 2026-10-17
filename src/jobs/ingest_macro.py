"""Daily macro ingestion: FRED series (config/macro.yaml) into macro_observations.

Each run re-downloads the window from start_date and upserts it, so FRED revisions
(e.g. CPI, claims) overwrite the stored value. A series that fails is reported and
the others still load; the job fails only when every series fails.
"""
from __future__ import annotations

from datetime import date

from src.adapters.fred import FredAdapter
from src.config import _load_yaml
from src.quality.run_metadata import job_run
from src.storage import curated


def main() -> None:
    cfg = _load_yaml("macro.yaml")
    start = date.fromisoformat(cfg["start_date"])
    with job_run("ingest_macro") as em:
        adapter = FredAdapter()
        written, failed = 0, {}
        for series_id in cfg["series"]:
            try:
                obs = adapter.fetch(series_id, start)
            except Exception as exc:  # noqa: BLE001 — reported below
                failed[series_id] = f"{type(exc).__name__}: {exc}"[:200]
                continue
            written += curated.upsert_macro(obs)
        em.emit(
            "WARN" if failed else "INFO",
            "rows_written",
            "macro_observations",
            payload={"rows": written, "series": len(cfg["series"]) - len(failed), "failed": failed},
        )
        if failed and len(failed) == len(cfg["series"]):
            raise RuntimeError(f"every FRED series failed, e.g. {next(iter(failed.values()))}")


if __name__ == "__main__":
    main()
