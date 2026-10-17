"""Name aliases per ticker, used to decide whether a news article is about the company.

Finnhub tags every article returned by /company-news with the ticker we asked for
("related"), even when the story only mentions it in passing or not at all (about a
third of articles). The news_relevant view keeps an article only when its headline
or summary matches one of these patterns (Postgres regular expressions):

- the company's core name ("Thermo Fisher Scientific", "Airbnb") and its first two
  words ("Thermo Fisher"), case-insensitive. A first word alone is too ambiguous
  ("Boston", "Jack"), so short names people use ("Disney") go in the overrides;
- the ticker as an uppercase word when it has 3+ letters ("NVDA"), or in any length
  as a cashtag or exchange prefix ("$M", "NYSE: M", "(M)").

config/news_aliases.yaml adds names the legal name misses (Google for GOOGL...).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited",
    "plc", "holdings", "holding", "group", "sa", "s a", "nv", "n v", "ag", "se", "spa", "lp", "llc",
    "class", "a", "b", "c", "the", "adr", "ads", "sponsored", "common", "stock", "ordinary",
    "shares", "public", "de", "cv", "sab", "trust", "international", "intl", "s",
}
_JOINERS = {"of", "and", "&", "the", "de", "la", "for"}
# One-word core names too common to use as an alias on their own.
_GENERIC = {
    "american", "first", "general", "united", "national", "international", "global", "new",
    "north", "south", "west", "east", "central", "pacific", "atlantic", "royal", "bank", "capital",
    "energy", "health", "healthcare", "medical", "financial", "data", "digital", "advanced",
    "applied", "western", "eastern", "southern", "northern", "great", "star", "sun", "world",
    "home", "life", "power", "air", "gold", "silver", "silvercrest", "texas", "smart", "the",
    "on", "gen", "jazz", "ally", "match", "target", "visa", "block", "snap", "box", "zoom",
    "telecom", "technology", "technologies", "systems", "solutions", "industries", "resources",
    "partners", "services", "brands", "foods", "pharmaceuticals", "therapeutics", "bio",
    "biosciences", "semiconductor", "software", "networks", "communications", "entertainment",
    "resorts", "materials", "properties", "realty", "insurance", "motors", "airlines", "steel",
    "mining", "oil", "gas", "water", "electric", "public", "standard", "premier", "summit",
    "alpha", "omega", "delta", "apex", "core", "nova", "prime", "true", "best", "dollar",
    "universal", "liberty", "freedom", "pioneer", "frontier", "heritage", "union", "federal",
    "community", "citizens", "people", "peoples", "republic", "southwest", "northwest",
}
# Ticker strings that are ordinary uppercase words or acronyms in news copy.
_TICKER_WORDS = {"ALL", "ARE", "NOW", "CAR", "GEN", "ASH", "CERT", "JAZZ", "IT", "ON", "AI", "BIG",
                 "FUN", "LOW", "KEY", "ONE", "TWO", "HAS", "GDS", "AES", "EOG", "SPY", "USA", "CEO",
                 "CFO", "IPO", "ETF", "GDP", "CPI", "FED", "SEC", "EPS"}

_PG_SPECIAL = re.compile(r"([.^$*+?()\[\]{}|\\])")


def _esc(s: str) -> str:
    return _PG_SPECIAL.sub(r"\\\1", s)


def core_name(name: str) -> list[str]:
    """Legal name -> words without punctuation or corporate suffixes."""
    s = name.replace("´", "'").replace("’", "'")
    s = re.sub(r"[^A-Za-z0-9&' \-]", " ", s)
    words = [w for w in s.split() if w]
    while words and (words[-1].lower().strip("'") in _SUFFIXES or len(words[-1]) <= 2 and len(words) > 1):
        words.pop()
    if words and words[0].lower() == "the":
        words = words[1:]
    return words


def patterns_for(ticker: str, name: str, extra: Iterable[str] = ()) -> list[tuple[str, bool]]:
    """(postgres_regex, case_sensitive) pairs for one ticker."""
    out: list[tuple[str, bool]] = []
    words = core_name(name)
    names = set(extra)
    if words:
        names.add(" ".join(words))
        if len(words) >= 2 and words[1].lower() not in _JOINERS:
            names.add(" ".join(words[:2]))
    for n in sorted(names):
        if len(n) >= 3 and n.lower() not in _GENERIC:
            out.append((r"\m" + _esc(n).replace("'", "['’]?") + r"\M", False))
    t = _esc(ticker)
    out.append((r"(\$|\m(NYSE|NASDAQ|Nasdaq|NYSEAMERICAN|AMEX)\s*:\s*|\()" + t + r"\M", True))
    if len(ticker) >= 3 and ticker.isalpha() and ticker not in _TICKER_WORDS:
        out.append((r"\m" + t + r"\M", True))
    return out
