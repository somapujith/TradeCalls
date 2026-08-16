"""Event-day caution flag for trade_setup alerts — wires the previously-
disconnected app/news/ module (aggregator + tagger) into the alert path.

Per user decision: flag, don't suppress — a setup with recent news still
alerts, just with a caution note attached so the caller can judge gap
risk themselves rather than the system silently withholding a signal.

Not a structured earnings/board-meeting calendar (no free official NSE
source for that, per docs/engine.md's Zero-Budget Constraint) — this is
"is there recent news mentioning this symbol at all," a coarser signal.
Recent = published within RECENT_NEWS_WINDOW_DAYS of the check.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.news.aggregator import fetch_all
from app.news.models import NewsItem
from app.news.tagger import tag_items

RECENT_NEWS_WINDOW_DAYS = 3
CAUTION_TEXT_MAX_LEN = 120


def build_event_caution_index(session: Session, *, limit: int = 100) -> dict[str, NewsItem]:
    """Fetches + tags recent news once, returns the most recent tagged item
    per symbol. Call once per scheduler run (not per-symbol) and pass the
    result to event_caution_for_symbol for each alert — avoids re-fetching
    every news source once per stock.
    """
    items = tag_items(session, fetch_all(limit=limit))
    by_symbol: dict[str, NewsItem] = {}
    for item in items:
        if item.symbol_hint is None:
            continue
        existing = by_symbol.get(item.symbol_hint)
        if existing is None or item.published_at > existing.published_at:
            by_symbol[item.symbol_hint] = item
    return by_symbol


def event_caution_for_symbol(
    symbol: str, index: dict[str, NewsItem], *, as_of: datetime | None = None
) -> str | None:
    """None if no recent tagged news for `symbol`, else a short caution
    string (headline + source, truncated) suitable for
    telegram.format_trade_setup_alert's event_caution param."""
    item = index.get(symbol)
    if item is None:
        return None

    now = as_of or datetime.now(item.published_at.tzinfo)
    if now - item.published_at > timedelta(days=RECENT_NEWS_WINDOW_DAYS):
        return None

    headline = item.headline
    if len(headline) > CAUTION_TEXT_MAX_LEN:
        headline = headline[: CAUTION_TEXT_MAX_LEN - 1] + "…"
    return f"Recent news ({item.source}): {headline}"
