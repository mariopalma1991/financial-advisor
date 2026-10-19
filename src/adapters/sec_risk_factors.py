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


NEW_BELOW = 0.6       # a paragraph sharing less than this with every prior one is a new risk
SAME_FROM = 0.95      # at or above this it is the same paragraph (years and amounts aside)


def _words(p: str) -> set[str]:
    """Comparison key: words only, so renumbered years and amounts don't count as edits."""
    return set(_WORD.findall(p.lower()))


def _best_match(p: set[str], others: list[set[str]]) -> float:
    """Highest Jaccard overlap of a paragraph's words with any paragraph of the other year."""
    return max((len(p & o) / len(p | o) for o in others if p or o), default=0.0)


def compare(current: list[str], prior: list[str]) -> dict[str, Any]:
    """Cosine similarity of the two sections' word counts (1 = unchanged), and each
    paragraph matched to its closest one in the other year: new (overlap below
    NEW_BELOW), reworded, or the same. Removed = prior paragraphs with no match."""
    a, b = Counter(_WORD.findall(" ".join(current).lower())), Counter(_WORD.findall(" ".join(prior).lower()))
    dot = sum(v * b[k] for k, v in a.items())
    na, nb = sqrt(sum(v * v for v in a.values())), sqrt(sum(v * v for v in b.values()))
    cw, pw = [_words(p) for p in current], [_words(p) for p in prior]
    cur_best = [_best_match(w, pw) for w in cw]
    added = [p for p, m in zip(current, cur_best) if m < NEW_BELOW]
    removed = [p for p, w in zip(prior, pw) if _best_match(w, cw) < NEW_BELOW]
    return {
        "similarity": round(dot / (na * nb), 4) if na and nb else None,
        "words": sum(len(p.split()) for p in current), "prior_words": sum(len(p.split()) for p in prior),
        "paragraphs": len(current), "prior_paragraphs": len(prior),
        "n_added": len(added), "n_reworded": sum(NEW_BELOW <= m < SAME_FROM for m in cur_best),
        "n_removed": len(removed), "added": added, "removed": removed,
    }


def latest_10ks(edgar: EdgarClient, cik: int, on_or_before: str, n: int = 2) -> list[dict[str, Any]]:
    """The last n annual reports filed on or before a day, newest first. Busy filers
    (banks issuing notes daily) push older 10-Ks out of the recent list into extra
    submission files, read only when needed."""
    doc = edgar.submissions_json(cik)
    rows = [r for r in parse_recent_filings(doc, "1900-01-01", FORMS) if r["filing_date"] <= on_or_before]
    for f in (doc.get("filings") or {}).get("files") or []:
        if len(rows) >= n:
            break
        older = edgar._get(f"{edgar.DATA}/submissions/{f['name']}").json()
        rows += [r for r in parse_recent_filings({"filings": {"recent": older}}, "1900-01-01", FORMS)
                 if r["filing_date"] <= on_or_before]
    return sorted(rows, key=lambda r: r["filing_date"], reverse=True)[:n]


def fetch_item_1a(edgar: EdgarClient, cik: int, filing: dict[str, Any]) -> list[str]:
    acc = filing["accession"].replace("-", "")
    return item_1a(html_to_paragraphs(edgar.text(cik, acc, filing["primary"])))
