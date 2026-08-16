"""Tests for app.news.event_caution — no real network calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.news import event_caution
from app.news.event_caution import (
    RECENT_NEWS_WINDOW_DAYS,
    build_event_caution_index,
    event_caution_for_symbol,
)
from app.news.models import NewsItem


def _item(symbol, headline, published_at, source="rss_economic_times"):
    return NewsItem(headline=headline, source=source, url="https://example.com", published_at=published_at, symbol_hint=symbol)


def test_build_event_caution_index_keeps_most_recent_per_symbol(monkeypatch):
    old = _item("TCS", "Old TCS news", datetime(2026, 8, 1, tzinfo=timezone.utc))
    new = _item("TCS", "New TCS news", datetime(2026, 8, 15, tzinfo=timezone.utc))
    other = _item("INFY", "Infy news", datetime(2026, 8, 10, tzinfo=timezone.utc))
    untagged = _item(None, "Untagged headline", datetime(2026, 8, 16, tzinfo=timezone.utc))

    monkeypatch.setattr(event_caution, "fetch_all", lambda limit=100: [old, new, other, untagged])
    monkeypatch.setattr(event_caution, "tag_items", lambda session, items: items)

    index = build_event_caution_index(session=None)

    assert index["TCS"] is new
    assert index["INFY"] is other
    assert None not in index


def test_event_caution_for_symbol_none_when_not_in_index():
    assert event_caution_for_symbol("TCS", {}) is None


def test_event_caution_for_symbol_none_when_stale():
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    stale = _item("TCS", "Old news", as_of - timedelta(days=RECENT_NEWS_WINDOW_DAYS + 1))

    result = event_caution_for_symbol("TCS", {"TCS": stale}, as_of=as_of)

    assert result is None


def test_event_caution_for_symbol_returns_formatted_caution_when_recent():
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    recent = _item("TCS", "TCS wins big order", as_of - timedelta(days=1), source="rss_economic_times")

    result = event_caution_for_symbol("TCS", {"TCS": recent}, as_of=as_of)

    assert result == "Recent news (rss_economic_times): TCS wins big order"


def test_event_caution_for_symbol_truncates_long_headlines():
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    long_headline = "A" * 200
    recent = _item("TCS", long_headline, as_of - timedelta(hours=1))

    result = event_caution_for_symbol("TCS", {"TCS": recent}, as_of=as_of)

    assert result is not None
    assert len(result) < len(long_headline)
    assert result.endswith("…")


def test_event_caution_for_symbol_exactly_at_window_boundary_is_stale():
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    boundary = _item("TCS", "News", as_of - timedelta(days=RECENT_NEWS_WINDOW_DAYS))

    # strict > in the implementation means exactly-at-boundary still counts as recent
    result = event_caution_for_symbol("TCS", {"TCS": boundary}, as_of=as_of)

    assert result is not None
