"""NewsAPI.org free dev tier — https://newsapi.org/docs/endpoints/everything

Free tier: 100 requests/day, dev-use only (not for production/client-side
use per NewsAPI's terms), ~1 month historical data. Needs
NEWS_API_NEWSAPI_KEY in .env — blank by default, see .env.example.
"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

from app.config import settings
from app.news.models import NewsItem

logger = logging.getLogger(__name__)

_EVERYTHING_URL = "https://newsapi.org/v2/everything"
_QUERY = "NSE India stock market"  # broad NSE-relevant query; not per-symbol in v1


def fetch_recent(limit: int = 50) -> list[NewsItem]:
    """Fetch recent NSE-relevant articles from NewsAPI.org's /everything endpoint.

    Never raises: returns [] and logs a warning on missing API key, network
    failure, non-200 response, or unexpected payload shape.
    """
    if not settings.news_api_newsapi_key:
        logger.info("NEWS_API_NEWSAPI_KEY not configured — skipping NewsAPI source")
        return []

    try:
        response = requests.get(
            _EVERYTHING_URL,
            params={
                "q": _QUERY,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": min(limit, 100),
                "apiKey": settings.news_api_newsapi_key,
            },
            timeout=settings.news_request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # network error, timeout, non-200, bad JSON
        logger.warning("NewsAPI fetch failed: %s", exc)
        return []

    articles = payload.get("articles")
    if not isinstance(articles, list):
        logger.warning("NewsAPI response missing 'articles' list: %r", payload.get("status"))
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
            items.append(
                NewsItem(
                    headline=headline,
                    source="newsapi",
                    url=url,
                    published_at=published_at,
                    summary=article.get("description"),
                    raw_text=article.get("content"),
                )
            )
        except Exception as exc:  # malformed individual article — skip, don't abort the batch
            logger.warning("NewsAPI article skipped (malformed): %s", exc)
            continue

    return items
