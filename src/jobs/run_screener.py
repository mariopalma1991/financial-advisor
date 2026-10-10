"""Daily screener run over both tiers -> screener_results (+ sanity check)."""
from __future__ import annotations

from datetime import date

from src.quality.checks import check_screener_sanity
from src.quality.run_metadata import job_run
from src.screener.engine import run_screener
from src.storage import curated


def main() -> None:
    with job_run("run_screener") as em:
        results = run_screener(date.today())
        n = curated.insert_screener_results(results)
        matched = [r for r in results if r["matched"]]
        em.emit(
            "INFO",
            "rows_written",
            "screener_results",
            payload={"rows": n, "matched": len(matched)},
        )
        for m in matched:
            em.emit(
                "INFO",
                "screen_matched",
                ticker=m["ticker"],
                message=f"{m['ticker']} matched {m['screen_name']}",
                payload={"screen": m["screen_name"], "metrics": m["metrics_json"]},
            )
        check_screener_sanity(em)


if __name__ == "__main__":
    main()
