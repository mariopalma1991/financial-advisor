"""Daily filing enrichment from SEC EDGAR (src/adapters/sec_edgar.py).

For filings already in raw_filings that have no `enriched` flag yet:
- 8-K / 6-K: 8-K item codes (what happened: 2.02 results, 5.02 officer change...),
  FinBERT sentiment of the press release (EX-99.x) or the main document; the text
  itself is archived as Parquet (filings_text, one file per filing day);
- Form 4: each transaction parsed into buy/sell value, net shares, owner role and
  the 10b5-1 plan flag.
Results go into payload_json; the filing_events / insider_trades views read them.
Runs outside the Finnhub group (EDGAR has its own 10 requests/s limit).
Usage: python -m src.jobs.enrich_filings --days 3 [--max-minutes 100]
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from typing import Any

from src.adapters.sec_edgar import EdgarClient, html_to_text, parse_form4, parse_index_url, pick_text_document
from src.quality.run_metadata import job_run
from src.storage import archive
from src.storage.supabase_client import pg_connection
from src.transforms.news_scoring import finbert_scores

EVENT_FORMS = ("8-K", "8-K/A", "6-K", "6-K/A")
INSIDER_FORMS = ("4", "4/A")
_CHUNK = 50  # filings per write


def select_pending(cur, days: int) -> list[tuple]:
    cur.execute(
        "select id, ticker, form_type, filed_at, url from raw_filings "
        "where filed_at >= now() - make_interval(days => %s) and form_type = any(%s) "
        "and not coalesce(payload_json, '{}'::jsonb) ? 'enriched' order by filed_at desc",
        (days, list(EVENT_FORMS + INSIDER_FORMS)),
    )
    return cur.fetchall()


def enrich_one(edgar: EdgarClient, sub: dict[str, Any], form: str, cik: int, acc: str) -> tuple[dict, str | None]:
    """-> (payload patch, text to archive or None)."""
    meta = sub.get(acc, {"items": [], "primary": ""})
    if form in INSIDER_FORMS:
        name = meta["primary"].split("/")[-1]
        if not name.endswith(".xml"):
            idx = edgar.index(cik, acc)
            name = next(i["name"] for i in idx["directory"]["item"] if i["name"].endswith(".xml"))
        return {"enriched": True, **parse_form4(edgar.text(cik, acc, name)).summary()}, None
    doc = pick_text_document(edgar.index(cik, acc), meta["primary"])
    text = html_to_text(edgar.text(cik, acc, doc)) if doc else ""
    return {"enriched": True, "items": meta["items"], "text_doc": doc}, text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--max-minutes", type=float, default=50)
    args = ap.parse_args()
    deadline = time.monotonic() + args.max_minutes * 60
    with job_run("enrich_filings") as em, pg_connection() as conn:
        conn.autocommit = True
        cur = conn.cursor()
        pending = select_pending(cur, args.days)
        by_cik: dict[int, list] = defaultdict(list)
        for row in pending:
            parsed = parse_index_url(row[4])
            if parsed:
                by_cik[parsed[0]].append((*row, parsed[1]))
        edgar, s3 = EdgarClient(), archive.s3_client()
        done, errors, samples, stopped = 0, 0, [], False
        batch: list[tuple[int, dict, str | None, Any, str]] = []

        def flush() -> None:
            texts = [(i, t) for i, (_, _, t, _, _) in enumerate(batch) if t]
            if texts:
                for (i, _), s in zip(texts, finbert_scores([t[:3000] for _, t in texts])):
                    batch[i][1].update(sentiment=s, sentiment_model="finbert")
            cur.executemany(
                "update raw_filings set payload_json = coalesce(payload_json, '{}'::jsonb) || %s::jsonb where id = %s",
                [(json.dumps(p), id_) for id_, p, _, _, _ in batch],
            )
            by_day: dict[str, list[dict]] = defaultdict(list)
            for id_, p, t, filed_at, acc in batch:
                if t:
                    by_day[filed_at.date().isoformat()].append(
                        {"accession": acc, "filing_id": id_, "items": ",".join(p.get("items", [])), "text": t})
            sizes = {d: archive.upsert_partition("filings_text", d, rows, key=("accession",), s3=s3)[1]
                     for d, rows in by_day.items()}
            if sizes:
                archive.update_manifest("filings_text", sizes, "filed_date", s3=s3)
            batch.clear()

        for cik, rows in by_cik.items():
            if time.monotonic() > deadline:
                stopped = True
                break
            try:
                sub = edgar.submissions(cik)
            except Exception as exc:  # noqa: BLE001 — counted and reported
                errors += len(rows)
                samples.append(f"CIK {cik}: {type(exc).__name__}: {exc}"[:160])
                continue
            for id_, _ticker, form, filed_at, _url, acc in rows:
                try:
                    patch, text = enrich_one(edgar, sub, form, cik, acc)
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    if len(samples) < 5:
                        samples.append(f"{acc}: {type(exc).__name__}: {exc}"[:160])
                    continue
                batch.append((id_, patch, text, filed_at, acc))
                done += 1
                if len(batch) >= _CHUNK:
                    flush()
        if batch:
            flush()
        em.emit(
            "WARN" if errors or stopped else "INFO",
            "rows_written",
            "raw_filings enriched",
            payload={"rows": done, "pending": len(pending), "errors": errors,
                     "stopped_by_budget": stopped, "error_samples": samples[:5]},
        )
        if pending and not done:
            raise RuntimeError(f"no filing could be enriched, e.g. {samples[:1]}")


if __name__ == "__main__":
    main()
