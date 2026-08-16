"""Fetch + tag + classify + persist news — turns the previously-disconnected
collection modules (aggregator, tagger) plus classifier.py into rows in
news_articles (see db/models.py's NewsArticle). Dedup on url (unique
constraint) — a re-fetch of an already-seen article is a no-op, not an
error, so this is safe to run on every scheduler tick without growing
duplicates.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NewsArticle
from app.news.aggregator import fetch_all
from app.news.classifier import classify
from app.news.tagger import tag_items

logger = logging.getLogger(__name__)


def fetch_classify_and_persist(session: Session, *, limit: int = 100) -> int:
    """Returns the number of new rows inserted (skips ones already present
    by url). Caller owns the transaction (commit/rollback), consistent
    with the rest of this codebase's db-touching functions."""
    items = tag_items(session, fetch_all(limit=limit))
    if not items:
        return 0

    existing_urls = {
        row[0] for row in session.execute(select(NewsArticle.url).where(NewsArticle.url.in_([i.url for i in items]))).all()
    }

    inserted = 0
    for item in items:
        if item.url in existing_urls:
            continue
        result = classify(item.headline, item.summary)
        session.add(
            NewsArticle(
                symbol=item.symbol_hint,
                headline=item.headline,
                source=item.source,
                url=item.url,
                summary=item.summary,
                published_at=item.published_at,
                event_type=result.event_type,
                sentiment=result.sentiment,
                severity=result.severity,
                confidence=result.confidence,
            )
        )
        existing_urls.add(item.url)  # guard against duplicate urls within the same fetch batch
        inserted += 1

    session.flush()
    logger.info("News fetch: %d new articles persisted (of %d fetched)", inserted, len(items))
    return inserted
