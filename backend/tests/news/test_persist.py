"""Tests for app.news.persist — in-memory SQLite, no real network/DB."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import NewsArticle
from app.db.session import Base
from app.news import persist
from app.news.models import NewsItem


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _item(headline, url, symbol=None, published_at=None):
    return NewsItem(
        headline=headline,
        source="rss_test",
        url=url,
        published_at=published_at or datetime(2026, 8, 17, 10, 0),
        symbol_hint=symbol,
    )


def test_persists_new_items_with_classification(monkeypatch, db_session):
    monkeypatch.setattr(persist, "fetch_all", lambda limit: [_item("RELIANCE faces court case", "https://x/1")])
    monkeypatch.setattr(persist, "tag_items", lambda session, items: [
        NewsItem(headline=i.headline, source=i.source, url=i.url, published_at=i.published_at, symbol_hint="RELIANCE")
        for i in items
    ])

    inserted = persist.fetch_classify_and_persist(db_session)
    db_session.commit()

    assert inserted == 1
    rows = db_session.scalars(select(NewsArticle)).all()
    assert len(rows) == 1
    assert rows[0].symbol == "RELIANCE"
    assert rows[0].event_type == "LEGAL"
    assert rows[0].sentiment == "NEGATIVE"


def test_dedupes_against_existing_url(monkeypatch, db_session):
    db_session.add(
        NewsArticle(
            symbol="RELIANCE", headline="Old", source="rss_test", url="https://x/1",
            published_at=datetime(2026, 8, 1), event_type="LEGAL", sentiment="NEGATIVE", severity=4, confidence=0.8,
        )
    )
    db_session.commit()

    monkeypatch.setattr(persist, "fetch_all", lambda limit: [_item("New headline same url", "https://x/1")])
    monkeypatch.setattr(persist, "tag_items", lambda session, items: items)

    inserted = persist.fetch_classify_and_persist(db_session)

    assert inserted == 0
    assert len(db_session.scalars(select(NewsArticle)).all()) == 1


def test_dedupes_within_same_batch(monkeypatch, db_session):
    monkeypatch.setattr(persist, "fetch_all", lambda limit: [
        _item("Headline A", "https://x/dup"), _item("Headline A again", "https://x/dup")
    ])
    monkeypatch.setattr(persist, "tag_items", lambda session, items: items)

    inserted = persist.fetch_classify_and_persist(db_session)

    assert inserted == 1


def test_empty_fetch_returns_zero(monkeypatch, db_session):
    monkeypatch.setattr(persist, "fetch_all", lambda limit: [])

    inserted = persist.fetch_classify_and_persist(db_session)

    assert inserted == 0
