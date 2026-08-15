"""News data-collection module for NSE-listed stock news, free-tier only.

Built ahead of the news-catalyst score component (docs/engine.md#scoring
lists it explicitly deferred in v1 — VWAP and news catalyst excluded,
remaining weight renormalized). This module is standalone: it collects,
normalizes, dedupes, and symbol-tags news, but nothing here is imported by
app/breakout/scoring.py or the breakout engine. Wiring it into scoring is a
future task, not this one.

Zero-budget constraint (docs/engine.md#zero-budget-constraint): every source
here is either a free-tier API (needs a user-supplied key, blank by default)
or an anonymous scrape of a public page/feed. No paid APIs, no paid scraping
services.

Submodules:
- models.py               — NewsItem dataclass, the common shape every source returns.
- api_newsapi.py           — NewsAPI.org free dev tier (needs NEWS_API_NEWSAPI_KEY).
- api_gnews.py              — GNews.io free tier (needs NEWS_API_GNEWS_KEY).
- scraper_cnbc_awaaz.py     — HTML scraper for CNBC Awaaz (hindi.cnbctv18.com), no official free API exists.
- scraper_rss_fallback.py   — RSS feed reader (Economic Times markets, stdlib XML parsing), fallback source.
- aggregator.py             — calls every source, dedupes, returns merged + sorted-by-recency list.
- tagger.py                 — keyword-based symbol tagger against app.db.models.Stock. Standalone utility,
                               not called by the aggregator or by scoring — a future integration point.

Every source module exposes fetch_recent(limit: int = 50) -> list[NewsItem]
and never raises on network/parse failure — logs a warning and returns [].
Scraper HTML structure is verified against the live site as of 2026-08-16
but WILL break when the target site redesigns; that's expected, not a bug.
"""
