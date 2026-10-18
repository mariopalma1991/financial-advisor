"""SEC "Insider Transactions Data Sets": every Form 3/4/5 of a quarter as tab-separated
tables in one zip (https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets).
Used to give the insider-buying factor history from before our own Form 4 loads
(src/jobs/backfill_insider_purchases.py). Only open-market purchases (code P) are kept,
summed per filing the same way parse_form4 sums them in sec_edgar.py.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Any

URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"


def _date(v: str | None) -> str | None:
    """'31-MAR-2025' or '2025-03-31' -> '2025-03-31'."""
    v = (v or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.title() if fmt.startswith("%d") else v, fmt).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _rows(z: zipfile.ZipFile, name: str):
    member = next((n for n in z.namelist() if n.upper().endswith(name)), None)
    if member is None:
        raise ValueError(f"{name} not in the zip")
    with z.open(member) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), delimiter="\t",
                                  quoting=csv.QUOTE_NONE)


def _role(rel: str | None, title: str | None) -> str:
    rel = (rel or "").lower()
    if "officer" in rel:
        return title or "officer"
    if "director" in rel:
        return "director"
    if "tenpercent" in rel.replace(" ", "").replace("%", "percent") or "10%" in rel:
        return "10% owner"
    return "other"


def parse_purchases(zip_bytes: bytes, cik_to_tickers: dict[int, list[str]]) -> list[dict[str, Any]]:
    """One row per Form 4 (or 4/A) and ticker (share classes share a CIK) of a tracked
    issuer with open-market buys:
    accession, ticker, filed_at, owner, role, is_10b5_1 (None when the data set has
    no flag), buy_value, first_trade_date."""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    subs: dict[str, dict] = {}
    for r in _rows(z, "SUBMISSION.TSV"):
        if (r.get("DOCUMENT_TYPE") or "") not in ("4", "4/A"):
            continue
        try:
            cik = int(r.get("ISSUERCIK") or 0)
        except ValueError:
            continue
        if cik in cik_to_tickers:
            plan = r.get("AFF10B5ONE")
            subs[r["ACCESSION_NUMBER"]] = {
                "accession": r["ACCESSION_NUMBER"], "tickers": cik_to_tickers[cik],
                "filed_at": _date(r.get("FILING_DATE")),
                "is_10b5_1": None if plan is None or plan == "" else plan.strip().lower() in ("1", "true", "y"),
            }
    buys: dict[str, dict] = {}
    for r in _rows(z, "NONDERIV_TRANS.TSV"):
        acc = r.get("ACCESSION_NUMBER")
        if acc not in subs or (r.get("TRANS_CODE") or "").strip() != "P":
            continue
        shares, price = _num(r.get("TRANS_SHARES")), _num(r.get("TRANS_PRICEPERSHARE"))
        b = buys.setdefault(acc, {"buy_value": 0.0, "first_trade_date": None})
        if shares and price:
            b["buy_value"] += shares * price
        d = _date(r.get("TRANS_DATE"))
        if d and (b["first_trade_date"] is None or d < b["first_trade_date"]):
            b["first_trade_date"] = d
    owners: dict[str, dict] = {}
    for r in _rows(z, "REPORTINGOWNER.TSV"):
        acc = r.get("ACCESSION_NUMBER")
        if acc in buys and acc not in owners:  # first reporting owner, as parse_form4 does
            owners[acc] = {"owner": r.get("RPTOWNERNAME"),
                           "role": _role(r.get("RPTOWNER_RELATIONSHIP"), r.get("RPTOWNER_TITLE"))}
    out = []
    for acc, b in buys.items():
        if b["buy_value"] <= 0 or not subs[acc]["filed_at"]:
            continue
        sub = {k: v for k, v in subs[acc].items() if k != "tickers"}
        for t in subs[acc]["tickers"]:
            out.append({**sub, "ticker": t, **owners.get(acc, {"owner": None, "role": "other"}),
                        "buy_value": round(b["buy_value"], 2), "first_trade_date": b["first_trade_date"]})
    return out
