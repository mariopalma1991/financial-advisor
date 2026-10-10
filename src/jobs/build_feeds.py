"""Build versioned feeds for the analysis tier -> research_packages."""
from __future__ import annotations

from src.feeds.builder import build_all
from src.quality.run_metadata import job_run


def main() -> None:
    with job_run("build_feeds") as em:
        n = build_all()
        em.emit("INFO", "rows_written", "research_packages", payload={"rows": n})


if __name__ == "__main__":
    main()
