"""SEC EDGAR: the filings list (raw_filings) and their content.

EDGAR (free, 10 requests/s, a contact User-Agent is required) gives:
- /files/company_tickers.json: ticker -> CIK for every SEC registrant;
- /submissions/CIK##########.json: per company, its recent filings (form, dates,
  8-K item codes, primary document); one call covers all of a company's filings;
- the filing folder's index.json: documents, to find the press release (EX-99.1);
- Form 4 XML: each insider transaction (code, shares, price, 10b5-1 plan flag).
"""
from __future__ import annotations

import html
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.adapters.base import source_retry

_MIN_GAP_S = 0.12  # stay under EDGAR's 10 requests/s
_URL_RE = re.compile(r"/edgar/data/(\d+)/(\d{18})/")

# 8-K items (https://www.sec.gov/fast-answers/answersform8khtm.html), the ones agents care about.
ITEM_LABELS = {
    "1.01": "material agreement", "1.02": "agreement terminated", "1.03": "bankruptcy",
    "1.05": "cybersecurity incident", "2.01": "acquisition or disposal completed",
    "2.02": "results of operations", "2.03": "new debt obligation", "2.04": "debt acceleration",
    "2.05": "exit or restructuring costs", "2.06": "material impairment",
    "3.01": "delisting notice", "3.02": "unregistered equity sale", "3.03": "security holder rights changed",
    "4.01": "auditor change", "4.02": "prior financials unreliable", "5.01": "change in control",
    "5.02": "officer or director change", "5.03": "bylaws amended", "5.07": "shareholder vote",
    "7.01": "regulation FD disclosure", "8.01": "other event", "9.01": "exhibits",
}

# Form 4 transaction codes; P and S are open-market trades, the rest mostly mechanics.
TX_CODES = {"P": "buy", "S": "sell", "A": "award", "M": "option exercise", "F": "tax withholding",
            "G": "gift", "D": "disposition to issuer", "C": "conversion", "X": "option exercise"}


def parse_index_url(url: str | None) -> tuple[int, str] | None:
    """EDGAR index URL -> (cik, accession without dashes)."""
    m = _URL_RE.search(url or "")
    return (int(m.group(1)), m.group(2)) if m else None


def parse_submissions(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """accession (no dashes) -> {items: [...], primary: str} from a submissions JSON."""
    recent = (doc.get("filings") or {}).get("recent") or {}
    accs = recent.get("accessionNumber") or []
    items = recent.get("items") or [""] * len(accs)
    prim = recent.get("primaryDocument") or [""] * len(accs)
    out = {}
    for a, it, p in zip(accs, items, prim):
        out[a.replace("-", "")] = {
            "items": sorted({i.strip() for i in (it or "").split(",") if i.strip()}),
            "primary": p or "",
        }
    return out


def parse_company_tickers(doc: dict[str, Any]) -> dict[str, int]:
    """company_tickers.json -> {ticker: cik}; class shares use '-' (BRK-B)."""
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in doc.values()}


def parse_sic(doc: dict[str, Any]) -> tuple[int | None, str | None]:
    """Submissions JSON -> (SIC code, its description); (None, None) for people and funds."""
    try:
        sic = int(doc.get("sic") or 0) or None
    except (TypeError, ValueError):
        sic = None
    return sic, (doc.get("sicDescription") or None) if sic else None


def parse_recent_filings(doc: dict[str, Any], since: str, forms: set[str]) -> list[dict[str, Any]]:
    """Filings in a submissions JSON filed on/after `since` (YYYY-MM-DD) whose form is in `forms`."""
    recent = (doc.get("filings") or {}).get("recent") or {}
    accs = recent.get("accessionNumber") or []

    def col(name: str) -> list[Any]:
        return recent.get(name) or [""] * len(accs)

    out = []
    for acc, form, day, accepted, items, prim, rep in zip(
        accs, col("form"), col("filingDate"), col("acceptanceDateTime"),
        col("items"), col("primaryDocument"), col("reportDate"),
    ):
        if not day or day < since or form not in forms:
            continue
        out.append({
            "accession": acc, "form": form, "filing_date": day, "accepted": accepted or None,
            "items": sorted({i.strip() for i in (items or "").split(",") if i.strip()}),
            "primary": prim or "", "report_date": rep or None,
        })
    return out


def pick_text_document(index: dict[str, Any], primary: str) -> str:
    """Press release exhibit (EX-99.x) when present, else the primary document."""
    names = [i.get("name", "") for i in (index.get("directory") or {}).get("item", [])]
    for n in sorted(names):
        if re.search(r"(ex|exhibit)[-_]?99", n, re.IGNORECASE) and n.lower().endswith((".htm", ".html", ".txt")):
            return n
    return primary


# SGML wrapper EDGAR leaves at the top of a document: "EX-99.1 2 file.htm EX-99.1 Document"
_SGML_HEAD = re.compile(r"^(?P<type>[\w./-]+) \d+ \S+\.(?:htm|html|txt)(?: (?P=type))?(?: Document)? ", re.IGNORECASE)


def html_to_text(raw: str, limit: int = 6000) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return _SGML_HEAD.sub("", s, count=1)[:limit]


def _f(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def _t(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    v = el.find("value")
    return ((v.text if v is not None else el.text) or "").strip() or None


@dataclass
class Form4:
    owner: str | None
    role: str                      # director / officer title / 10% owner / other
    is_10b5_1: bool                # trades under a pre-arranged plan (not a timing signal)
    transactions: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        buy = sum(t["value"] or 0 for t in self.transactions if t["code"] == "P")
        sell = sum(t["value"] or 0 for t in self.transactions if t["code"] == "S")
        return {
            "owner": self.owner, "role": self.role, "is_10b5_1": self.is_10b5_1,
            "codes": sorted({t["code"] for t in self.transactions}),
            "buy_value": round(buy, 2), "sell_value": round(sell, 2),
            "net_shares": round(sum(t["shares"] * (1 if t["acquired"] else -1)
                                    for t in self.transactions if t["shares"]), 2),
        }


def parse_form4(xml_text: str) -> Form4:
    root = ET.fromstring(xml_text)
    owner_el = root.find("reportingOwner")
    owner = _t(owner_el, "reportingOwnerId/rptOwnerName")
    rel = owner_el.find("reportingOwnerRelationship") if owner_el is not None else None

    def flag(tag: str) -> bool:
        return (_t(rel, tag) or "").lower() in ("1", "true")

    if flag("isOfficer"):
        role = _t(rel, "officerTitle") or "officer"
    elif flag("isDirector"):
        role = "director"
    elif flag("isTenPercentOwner"):
        role = "10% owner"
    else:
        role = "other"
    plan = (_t(root, "aff10b5One") or "").lower() in ("1", "true")
    txs = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _t(tx, "transactionCoding/transactionCode") or ""
        shares = _f(_t(tx, "transactionAmounts/transactionShares"))
        price = _f(_t(tx, "transactionAmounts/transactionPricePerShare"))
        txs.append({
            "code": code,
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if shares and price else None,
            "acquired": _t(tx, "transactionAmounts/transactionAcquiredDisposedCode") == "A",
            "date": _t(tx, "transactionDate"),
        })
    return Form4(owner=owner, role=role, is_10b5_1=plan, transactions=txs)


class EdgarClient:
    BASE = "https://www.sec.gov"
    DATA = "https://data.sec.gov"

    def __init__(self, user_agent: str | None = None, client: httpx.Client | None = None) -> None:
        ua = user_agent or os.getenv("SEC_USER_AGENT") or "financial-advisor github.com/mariopalma1991/financial-advisor"
        self._client = client or httpx.Client(timeout=30, headers={"User-Agent": ua}, follow_redirects=True)
        self._last = 0.0

    @source_retry
    def _get(self, url: str) -> httpx.Response:
        wait = self._last + _MIN_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp

    def submissions_json(self, cik: int) -> dict[str, Any]:
        return self._get(f"{self.DATA}/submissions/CIK{cik:010d}.json").json()

    def submissions(self, cik: int) -> dict[str, dict[str, Any]]:
        return parse_submissions(self.submissions_json(cik))

    def company_tickers(self) -> dict[str, int]:
        return parse_company_tickers(self._get(f"{self.BASE}/files/company_tickers.json").json())

    def folder(self, cik: int, acc: str) -> str:
        return f"{self.BASE}/Archives/edgar/data/{cik}/{acc}"

    def index(self, cik: int, acc: str) -> dict[str, Any]:
        return self._get(f"{self.folder(cik, acc)}/index.json").json()

    def text(self, cik: int, acc: str, name: str) -> str:
        return self._get(f"{self.folder(cik, acc)}/{name}").text
