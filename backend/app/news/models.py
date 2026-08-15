"""Common data shape for every news source in this module — see app/news/__init__.py.

Every source (api_newsapi, api_gnews, scraper_cnbc_awaaz, scraper_rss_fallback)
normalizes into this one dataclass so aggregator.py can merge/dedupe/sort
across sources without caring where an item came from.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class NewsItem:
    headline: str
    source: str  # e.g. "newsapi", "gnews", "cnbc_awaaz", "rss_economic_times"
    url: str
    published_at: datetime
    summary: str | None = None
    raw_text: str | None = None
    symbol_hint: str | None = None  # str | None — news often isn't pre-tagged to a symbol
