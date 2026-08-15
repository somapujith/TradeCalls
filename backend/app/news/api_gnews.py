"""GNews.io free tier — https://docs.gnews.io

Free tier: 100 requests/day, 1 req/sec, up to 10 articles/request, 12-hour
publish delay, non-commercial use only. Needs NEWS_API_GNEWS_KEY in .env —
blank by default, see .env.example.
"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from app.config import settings
from app.news.models import NewsItem

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://gnews.io/api/v4/search"
_QUERY = "NSE India stock market"  # broad NSE-relevant query; not per-symbol in v1
_MAX_ARTICLES_PER_REQUEST = 10  # GNews free-tier cap


def fetch_recent(limit: int = 50) -> list[NewsItem]:
    """Fetch recent NSE-relevant articles from GNews.io's /search endpoint.

    Never raises: returns [] and logs a warning on missing API key, network
    failure, non-200 response, or unexpected payload shape.
    """
    if not settings.news_api_gnews_key:
        logger.info("NEWS_API_GNEWS_KEY not configured — skipping GNews source")
        return []

    try:
        response = requests.get(
            _SEARCH_URL,
            params={
                "q": _QUERY,
                "lang": "en",
                "sortby": "publishedAt",
                "max": min(limit, _MAX_ARTICLES_PER_REQUEST),
                "apikey": settings.news_api_gnews_key,
            },
            timeout=settings.news_request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # network error, timeout, non-200, bad JSON
        logger.warning("GNews fetch failed: %s", exc)
        return []

    articles = payload.get("articles")
    if not isinstance(articles, list):
        logger.warning("GNews response missing 'articles' list: %r", payload)
        return []

    items: list[NewsItem] = []
    for article in articles[:limit]:
        try:
            headline = article["title"]
            url = article["url"]
            published_raw = article.get("publishedAt")
            published_at = (
                # tzinfo stripped: keeps this naive like the other sources' fallback
                # datetime.utcnow(), so aggregator.py can sort across sources without
                # offset-naive/offset-aware comparison errors.
                datetime.fromisoformat(published_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                if published_raw
                else datetime.utcnow()
            )
            source_name = (article.get("source") or {}).get("name")
            items.append(
                NewsItem(
                    headline=headline,
                    source="gnews",
                    url=url,
                    published_at=published_at,
                    summary=article.get("description"),
                    raw_text=article.get("content") or source_name,
                )
            )
        except Exception as exc:  # malformed individual article — skip, don't abort the batch
            logger.warning("GNews article skipped (malformed): %s", exc)
            continue

    return items
