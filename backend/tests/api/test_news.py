"""Tests for GET /api/news — see docs/api.md and app/api/news.py."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import NewsArticle


def make_news(
    db: Session,
    *,
    symbol: str | None = "RELIANCE",
    headline: str = "Headline",
    url: str = "https://example.com/1",
    published_at: datetime = datetime(2026, 8, 17, 10, 0),
    event_type: str = "LEGAL",
    sentiment: str = "NEGATIVE",
    severity: int = 4,
    confidence: float = 0.8,
) -> NewsArticle:
    article = NewsArticle(
        symbol=symbol,
        headline=headline,
        source="rss_test",
        url=url,
        summary=None,
        published_at=published_at,
        event_type=event_type,
        sentiment=sentiment,
        severity=severity,
        confidence=confidence,
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


def test_list_news_empty_when_none(client: TestClient, db_session: Session) -> None:
    resp = client.get("/api/news")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_news_happy_path(client: TestClient, db_session: Session) -> None:
    make_news(db_session, headline="RELIANCE faces court case")

    resp = client.get("/api/news")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["headline"] == "RELIANCE faces court case"
    assert body[0]["event_type"] == "LEGAL"
    assert body[0]["sentiment"] == "NEGATIVE"
    assert body[0]["severity"] == 4


def test_list_news_filters_by_symbol(client: TestClient, db_session: Session) -> None:
    make_news(db_session, symbol="RELIANCE", url="https://example.com/1")
    make_news(db_session, symbol="TCS", url="https://example.com/2")

    resp = client.get("/api/news", params={"symbol": "TCS"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "TCS"


def test_list_news_symbol_filter_is_case_insensitive(client: TestClient, db_session: Session) -> None:
    make_news(db_session, symbol="RELIANCE", url="https://example.com/1")

    resp = client.get("/api/news", params={"symbol": "reliance"})

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_news_filters_by_min_severity(client: TestClient, db_session: Session) -> None:
    make_news(db_session, severity=1, url="https://example.com/low")
    make_news(db_session, severity=4, url="https://example.com/high")

    resp = client.get("/api/news", params={"min_severity": 3})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["severity"] == 4


def test_list_news_ordered_by_published_at_descending(client: TestClient, db_session: Session) -> None:
    make_news(db_session, url="https://example.com/old", published_at=datetime(2026, 8, 1))
    make_news(db_session, url="https://example.com/new", published_at=datetime(2026, 8, 17))

    resp = client.get("/api/news")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["url"] == "https://example.com/new"
    assert body[1]["url"] == "https://example.com/old"


def test_list_news_respects_limit(client: TestClient, db_session: Session) -> None:
    for i in range(5):
        make_news(db_session, url=f"https://example.com/{i}")

    resp = client.get("/api/news", params={"limit": 2})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_news_null_symbol_is_returned_normally(client: TestClient, db_session: Session) -> None:
    make_news(db_session, symbol=None)

    resp = client.get("/api/news")

    assert resp.status_code == 200
    assert resp.json()[0]["symbol"] is None
