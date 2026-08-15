"""HTML scraper for CNBC Awaaz (CNBC-TV18's Hindi business news channel).

No free official API exists for CNBC Awaaz, so this scrapes the public
share-market listing page directly. Verified live as of 2026-08-16:
  - Domain: hindi.cnbctv18.com (confirmed via Wikipedia + direct HTTP 200;
    the naive guess "cnbcawaaz.cnbctv18.com" does NOT resolve).
  - Listing page: https://hindi.cnbctv18.com/share-market (HTTP 200,
    26 stories found on the verification fetch).
  - Headlines live in `<h2 class="story-title">`, wrapped by an `<a href=...>`
    with the article URL; publish time in a sibling
    `<span class="story-date"><time>Aug 15, 2026 1:58 PM</time></span>`.

This structure WILL change without notice (frontend redesigns, class-name
hashes like the `jsx-xxxxx` ones observed alongside `story-title` suggest a
CSS-in-JS build that reshuffles hashes on deploy — the semantic class names
tend to be more stable than the hashes, which is why we select on
`story-title` / `story-date` and not on the `jsx-*` classes). Any parsing
failure is caught, logged, and results in an empty list — never a raised
exception — since a broken scraper must not take down the caller (see
app/news/aggregator.py).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.news.models import NewsItem

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_HEADLINE_SELECTOR = "h2.story-title"
_DATE_FORMAT = "%b %d, %Y %I:%M %p"  # e.g. "Aug 15, 2026 1:58 PM"

_last_request_at: float = 0.0


def _respect_rate_limit() -> None:
    """Sleep just enough to keep at least news_scraper_delay_seconds between
    requests to this host — be a polite scraper, not a hammer."""
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    remaining = settings.news_scraper_delay_seconds - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _last_request_at = time.monotonic()


def _parse_published_at(story_anchor) -> datetime:
    time_tag = story_anchor.select_one("span.story-date time")
    if time_tag is None:
        return datetime.utcnow()
    try:
        return datetime.strptime(time_tag.get_text(strip=True), _DATE_FORMAT)
    except ValueError:
        return datetime.utcnow()


def fetch_recent(limit: int = 50) -> list[NewsItem]:
    """Scrape recent headlines from CNBC Awaaz's share-market listing page.

    Never raises: any request or parse failure is caught, logged, and
    results in an empty list.
    """
    url = f"{settings.news_cnbc_awaaz_base_url}{settings.news_cnbc_awaaz_section_path}"

    try:
        _respect_rate_limit()
        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=settings.news_request_timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:  # network error, timeout, non-200
        logger.warning("CNBC Awaaz fetch failed for %s: %s", url, exc)
        return []

    items: list[NewsItem] = []
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        headlines = soup.select(_HEADLINE_SELECTOR)
    except Exception as exc:  # HTML structure changed enough to break parsing entirely
        logger.warning("CNBC Awaaz parse failed (page structure likely changed): %s", exc)
        return []

    for headline_tag in headlines[:limit]:
        try:
            anchor = headline_tag.find_parent("a")
            if anchor is None or not anchor.get("href"):
                continue
            headline_text = headline_tag.get_text(strip=True)
            if not headline_text:
                continue
            items.append(
                NewsItem(
                    headline=headline_text,
                    source="cnbc_awaaz",
                    url=anchor["href"],
                    published_at=_parse_published_at(anchor),
                    summary=anchor.get("title") or headline_text,
                )
            )
        except Exception as exc:  # one malformed story block — skip, don't abort the batch
            logger.warning("CNBC Awaaz story block skipped (malformed): %s", exc)
            continue

    return items
