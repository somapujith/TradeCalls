"""Keyword-based symbol tagger — matches stocks.symbol/name against news text.

Standalone utility: this is what would eventually feed the news-catalyst
score component (docs/engine.md#scoring lists it deferred in v1), but it is
NOT called from app/breakout/scoring.py or the breakout engine. That wiring
is a future task, out of scope here.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Stock
from app.news.models import NewsItem

logger = logging.getLogger(__name__)


def _build_pattern(symbol: str, name: str | None) -> re.Pattern[str]:
    """Word-boundary, case-insensitive match on the symbol and (if present)
    the company name, so e.g. "TCS" doesn't match inside "TCSXYZ" but does
    match "TCS" or "Tata Consultancy Services"."""
    terms = [re.escape(symbol)]
    if name:
        terms.append(re.escape(name))
    alternation = "|".join(terms)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def load_symbol_patterns(session: Session) -> dict[str, re.Pattern[str]]:
    """One compiled regex per active stock, keyed by symbol. Callers that
    tag many items in a batch should build this once and reuse it rather
    than re-querying per item."""
    stocks = session.scalars(select(Stock).where(Stock.listing_status == "ACTIVE")).all()
    return {stock.symbol: _build_pattern(stock.symbol, stock.name) for stock in stocks}


def tag_symbol(item: NewsItem, patterns: dict[str, re.Pattern[str]]) -> str | None:
    """Return the first matching symbol found in the item's headline or
    summary, or None if no known symbol/company name appears. First match
    wins — a headline naming multiple stocks is tagged with whichever
    symbol's pattern happens to match first, not a ranked "best" match."""
    text = " ".join(part for part in (item.headline, item.summary) if part)
    for symbol, pattern in patterns.items():
        if pattern.search(text):
            return symbol
    return None


def tag_items(session: Session, items: list[NewsItem]) -> list[NewsItem]:
    """Return new NewsItem copies with symbol_hint populated where a match
    is found. Does not mutate the input list or its items."""
    patterns = load_symbol_patterns(session)
    if not patterns:
        logger.info("No active stocks found in stocks table — nothing to tag against")
        return list(items)

    tagged: list[NewsItem] = []
    for item in items:
        symbol = tag_symbol(item, patterns)
        tagged.append(
            NewsItem(
                headline=item.headline,
                source=item.source,
                url=item.url,
                published_at=item.published_at,
                summary=item.summary,
                raw_text=item.raw_text,
                symbol_hint=symbol,
            )
        )
    return tagged
