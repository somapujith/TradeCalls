"""RSS feed fallback source — general NSE-relevant financial news.

Verified live as of 2026-08-16:
  - Economic Times markets RSS: https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms
    returns HTTP 200 with a well-formed RSS 2.0 feed (confirmed via direct
    fetch — real, current article titles/links/pubDates observed).
  - Moneycontrol's RSS (https://www.moneycontrol.com/rss/latestnews.xml)
    returned HTTP 403 on every fetch attempt during verification, including
    with a browser User-Agent — likely bot-blocked at the edge/CDN. NOT
    wired in as a default feed for that reason; the feed list is
    configurable via NEWS_RSS_FEED_URLS (comma-separated) in case a working
    Moneycontrol (or other) feed URL is found later.

Uses only the standard library's xml.etree.ElementTree — no feedparser
dependency needed for a plain RSS 2.0 <item> structure.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests

from app.config import settings
from app.news.models import NewsItem

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_last_request_at: float = 0.0


def _respect_rate_limit() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    remaining = settings.news_scraper_delay_seconds - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _last_request_at = time.monotonic()


def _feed_urls() -> list[str]:
    return [url.strip() for url in settings.news_rss_feed_urls.split(",") if url.strip()]


def _parse_pub_date(raw: str | None) -> datetime:
    """Returns a naive datetime (tzinfo stripped) — matches the rest of the
    module's naive datetime.utcnow() fallback so aggregator.py can sort
    across sources without offset-naive/offset-aware comparison errors."""
    if not raw:
        return datetime.utcnow()
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return datetime.utcnow()


def _fetch_one_feed(feed_url: str, limit: int) -> list[NewsItem]:
    try:
        _respect_rate_limit()
        response = requests.get(
            feed_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/xml,text/xml,*/*"},
            timeout=settings.news_request_timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:  # network error, timeout, non-200 (e.g. bot-blocked feeds)
        logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
        return []

    items: list[NewsItem] = []
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        logger.warning("RSS parse failed for %s (malformed XML): %s", feed_url, exc)
        return []

    for entry in root.findall("./channel/item")[:limit]:
        try:
            title = entry.findtext("title")
            link = entry.findtext("link")
            if not title or not link:
                continue
            items.append(
                NewsItem(
                    headline=title.strip(),
                    source=f"rss:{feed_url}",
                    url=link.strip(),
                    published_at=_parse_pub_date(entry.findtext("pubDate")),
                    summary=entry.findtext("description"),
                )
            )
        except Exception as exc:  # one malformed <item> — skip, don't abort the feed
            logger.warning("RSS item skipped (malformed) from %s: %s", feed_url, exc)
            continue

    return items


def fetch_recent(limit: int = 50) -> list[NewsItem]:
    """Fetch recent items across all configured RSS feeds (NEWS_RSS_FEED_URLS,
    comma-separated; defaults to Economic Times markets).

    Never raises: any per-feed failure is caught, logged, and simply
    contributes no items from that feed — one broken feed doesn't block
    the others.
    """
    all_items: list[NewsItem] = []
    for feed_url in _feed_urls():
        all_items.extend(_fetch_one_feed(feed_url, limit))

    return all_items[:limit]
