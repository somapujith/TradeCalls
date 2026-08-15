"""Merges every news source into one deduped, recency-sorted list.

Calls all four sources (api_newsapi, api_gnews, scraper_cnbc_awaaz,
scraper_rss_fallback), each of which never raises — so a single failing
source just contributes zero items rather than breaking the aggregate.
"""
from __future__ import annotations

import logging
from difflib import SequenceMatcher

from app.news import api_gnews, api_newsapi, scraper_cnbc_awaaz, scraper_rss_fallback
from app.news.models import NewsItem

logger = logging.getLogger(__name__)

_HEADLINE_SIMILARITY_THRESHOLD = 0.85  # ratio above which two headlines are treated as duplicates

_SOURCES = (
    api_newsapi.fetch_recent,
    api_gnews.fetch_recent,
    scraper_cnbc_awaaz.fetch_recent,
    scraper_rss_fallback.fetch_recent,
)


def _is_duplicate(candidate: NewsItem, seen: list[NewsItem]) -> bool:
    for existing in seen:
        if candidate.url == existing.url:
            return True
        similarity = SequenceMatcher(None, candidate.headline.lower(), existing.headline.lower()).ratio()
        if similarity >= _HEADLINE_SIMILARITY_THRESHOLD:
            return True
    return False


def fetch_all(limit: int = 50) -> list[NewsItem]:
    """Fetch recent news from every source, dedupe by exact URL match or
    near-identical headline (fuzzy match, catches the same story syndicated
    across sources with slightly different wording), and return sorted by
    published_at descending (most recent first), truncated to `limit`.
    """
    collected: list[NewsItem] = []
    for fetch_recent in _SOURCES:
        try:
            collected.extend(fetch_recent(limit))
        except Exception as exc:  # belt-and-braces — sources shouldn't raise, but never let one break the batch
            logger.warning("News source %s raised unexpectedly: %s", fetch_recent.__module__, exc)

    collected.sort(key=lambda item: item.published_at, reverse=True)

    deduped: list[NewsItem] = []
    for item in collected:
        if not _is_duplicate(item, deduped):
            deduped.append(item)

    return deduped[:limit]
