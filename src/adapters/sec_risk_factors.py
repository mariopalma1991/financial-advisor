"""10-K risk factors (Item 1A) from SEC EDGAR, and how they changed from the year
before. Companies rarely rewrite this section; when they add or reword risks it
tends to come before bad news ("Lazy Prices", Cohen, Malloy and Nguyen 2020), so
the year-over-year difference is the signal, not the boilerplate itself.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from math import sqrt
from typing import Any

from src.adapters.sec_edgar import EdgarClient, parse_recent_filings

FORMS = {"10-K", "10-K405", "10-KT"}
MIN_WORDS = 12          # shorter lines are headings, page numbers, table cells
MAX_PARAGRAPH = 1200    # characters returned per changed paragraph
_BLOCK = re.compile(r"(?i)</?(p|div|br|tr|li|h[1-6]|table)\b[^>]*>")
_START = re.compile(r"(?i)^\W*item\s*1a\b\W*(risk\s+factors)?")
_END = re.compile(r"(?i)^\W*item\s*(1b|1c|2)\b")
_WORD = re.compile(r"[a-z][a-z'-]+")


def html_to_paragraphs(raw: str) -> list[str]:
    """Text blocks of an HTML filing, one per paragraph (block tags kept as breaks)."""
    s = re.sub(r"(?is)<(script|style|ix:header).*?</\1>", " ", raw)
    s = _BLOCK.sub("\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    return [p for p in (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in s.split("\n")) if p]


def item_1a(blocks: list[str]) -> list[str]:
    """Paragraphs of Item 1A. The heading also appears in the table of contents,
    so the longest span between an 'Item 1A' line and the next item wins."""
    best: list[str] = []
    for i, b in enumerate(blocks):
        if len(b) > 120 or not _START.match(b):
            continue
        body: list[str] = []
        for nxt in blocks[i + 1:]:
            if len(nxt) <= 120 and _END.match(nxt):
                break
            body.append(nxt)
        paras = [p for p in body if len(p.split()) >= MIN_WORDS]
        if sum(map(len, paras)) > sum(map(len, best)):
            best = paras
    return best


def _norm(p: str) -> str:
    """Comparison key: words only, so renumbered years and amounts don't count as edits."""
    return " ".join(_WORD.findall(p.lower()))


def compare(current: list[str], prior: list[str]) -> dict[str, Any]:
    """Cosine similarity of the two sections' word counts (1 = unchanged) and the
    paragraphs added or removed (compared on their words, ignoring numbers)."""
    a, b = Counter(_WORD.findall(" ".join(current).lower())), Counter(_WORD.findall(" ".join(prior).lower()))
    dot = sum(v * b[k] for k, v in a.items())
    na, nb = sqrt(sum(v * v for v in a.values())), sqrt(sum(v * v for v in b.values()))
    cur_keys, pri_keys = {_norm(p) for p in current}, {_norm(p) for p in prior}
    added = [p for p in current if _norm(p) not in pri_keys]
    removed = [p for p in prior if _norm(p) not in cur_keys]
    return {
        "similarity": round(dot / (na * nb), 4) if na and nb else None,
        "words": sum(len(p.split()) for p in current), "prior_words": sum(len(p.split()) for p in prior),
        "paragraphs": len(current), "prior_paragraphs": len(prior),
        "n_added": len(added), "n_removed": len(removed),
        "added": added, "removed": removed,
    }


def latest_10ks(edgar: EdgarClient, cik: int, on_or_before: str, n: int = 2) -> list[dict[str, Any]]:
    """The last n annual reports filed on or before a day, newest first."""
    rows = parse_recent_filings(edgar.submissions_json(cik), "1900-01-01", FORMS)
    rows = sorted((r for r in rows if r["filing_date"] <= on_or_before), key=lambda r: r["filing_date"],
                  reverse=True)
    return rows[:n]


def fetch_item_1a(edgar: EdgarClient, cik: int, filing: dict[str, Any]) -> list[str]:
    acc = filing["accession"].replace("-", "")
    return item_1a(html_to_paragraphs(edgar.text(cik, acc, filing["primary"])))
