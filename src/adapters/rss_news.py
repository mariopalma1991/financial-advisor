"""RSS/Atom news adapter — fetches financial headlines from configurable feeds.

Free RSS feeds provide headline-level news for the screening tier. Full-text
extraction and embeddings are reserved for the analysis tier (plan §5, §7).

Each article gets a stable article_id (SHA-256 of the URL) for deduplication.
The adapter returns RawNewsArticle objects; it never writes to the database.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any

import feedparser

from src.adapters.base import RawNewsArticle, source_retry

# Default financial RSS feeds — free, no API key required.
# Override by passing `feed_urls` to the constructor.
_DEFAULT_FEEDS: list[str] = [
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",          # MarketWatch
    "https://feeds.content.dowjones.io/public/rss/wsj_markets",            # WSJ Markets
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",  # CNBC Top News
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",   # CNBC Markets
    "https://feeds.reuters.com/reuters/businessNews",                      # Reuters Business
    "https://finance.yahoo.com/news/rssindex",                             # Yahoo Finance
]


def _make_article_id(url: str) -> str:
    """Stable article ID from URL — SHA-256 hex digest."""
    return hashlib.sha256(url.encode()).hexdigest()


def _parse_ts(entry: dict[str, Any]) -> datetime | None:
    """Extract published timestamp from a feed entry, trying common fields."""
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp and len(tp) >= 6:
            try:
                return datetime(*tp[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
    # Fallback: try string fields
    for field in ("published", "updated"):
        raw = entry.get(field)
        if raw:
            try:
                from dateutil.parser import parse as dt_parse
                return dt_parse(raw).replace(tzinfo=timezone.utc)
            except Exception:
                continue
    return None


class RssNewsAdapter:
    """Fetches news articles from RSS/Atom feeds.

    Usage:
        adapter = RssNewsAdapter()
        articles = adapter.fetch(since=date.today())
    """

    name = "rss"

    def __init__(self, feed_urls: list[str] | None = None) -> None:
        self._feeds = feed_urls or _DEFAULT_FEEDS

    @source_retry
    def _fetch_one(self, url: str) -> list[dict[str, Any]]:
        """Fetch and parse a single RSS feed. Returns raw entry dicts."""
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            # feedparser sets bozo on some warnings that still have entries;
            # only treat as failure when there are zero entries
            exc = parsed.bozo_exception
            raise RuntimeError(f"RSS feed {url} returned no entries: {exc}")
        return list(parsed.entries)

    def fetch(self, since: date | None = None) -> list[RawNewsArticle]:
        """Fetch articles from all configured feeds, optionally filtered by date.

        Args:
            since: If provided, only return articles published on or after this date.

        Returns:
            List of RawNewsArticle objects, deduplicated by article_id within this batch.
        """
        seen: set[str] = set()
        articles: list[RawNewsArticle] = []

        for feed_url in self._feeds:
            try:
                entries = self._fetch_one(feed_url)
            except Exception:
                # One failing feed shouldn't block the rest
                continue

            for entry in entries:
                url = entry.get("link") or entry.get("id") or ""
                if not url:
                    continue

                article_id = _make_article_id(url)
                if article_id in seen:
                    continue
                seen.add(article_id)

                published_at = _parse_ts(entry)
                if published_at is None:
                    published_at = datetime.now(timezone.utc)

                if since and published_at.date() < since:
                    continue

                headline = entry.get("title", "").strip()
                # Some feeds provide a summary/description as body preview
                summary = entry.get("summary") or entry.get("description") or ""

                articles.append(
                    RawNewsArticle(
                        source=self.name,
                        article_id=article_id,
                        published_at=published_at,
                        url=url,
                        headline=headline if headline else None,
                        body=summary if summary else None,
                        payload={
                            "feed_url": feed_url,
                            "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                        },
                    )
                )

        return articles