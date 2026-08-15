"""Tests for GET /api/universe — see docs/api.md.

Key contract points under test:
- Query param `as_of_date` is optional, defaulting to the latest trade_date
  present in daily_ohlcv (delegated to app.data.universe.get_universe).
- Response (array): symbol, sector, listing_status, close, avg_turnover_20d.
- No stored table — computed on request.
- Malformed as_of_date -> 422 (FastAPI's `date` query-param validation).
- Liquidity filter (price/turnover floors, ACTIVE-only) is app.data.universe's
  responsibility, already covered by tests/data/test_universe.py — these
  tests only cover the route's plumbing (param handling, response shape),
  monkeypatching app.data.universe.get_universe to isolate that.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import make_daily_bar, make_stock


def test_get_universe_empty_db_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/api/universe")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_universe_malformed_as_of_date_422(client: TestClient) -> None:
    resp = client.get("/api/universe", params={"as_of_date": "not-a-date"})

    assert resp.status_code == 422


def test_get_universe_as_of_date_before_any_data_returns_empty_list(
    client: TestClient, db_session: Session
) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_daily_bar(db_session, stock, date(2026, 6, 1), close=500.0)

    resp = client.get("/api/universe", params={"as_of_date": "2020-01-01"})

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_universe_response_mapping(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the route's response-construction logic (UniverseEntryOut
    field mapping) independently of app.data.universe's actual liquidity
    filtering, by stubbing get_universe with a fake matching its real
    dict-return shape (symbol, sector, listing_status, close, avg_turnover_20d).
    """
    make_stock(db_session, symbol="RELIANCE")

    def fake_get_universe(session, as_of_date):
        return [
            {
                "symbol": "RELIANCE",
                "sector": "Energy",
                "listing_status": "ACTIVE",
                "close": 500.0,
                "avg_turnover_20d": 25.0,
            }
        ]

    monkeypatch.setattr("app.api.universe.compute_universe", fake_get_universe)

    resp = client.get("/api/universe")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    entry = body[0]
    assert entry["symbol"] == "RELIANCE"
    assert entry["sector"] == "Energy"
    assert entry["listing_status"] == "ACTIVE"
    assert entry["close"] == 500.0
    assert entry["avg_turnover_20d"] == 25.0


def test_get_universe_nullable_sector(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_stock(db_session, symbol="RELIANCE", sector=None)

    def fake_get_universe(session, as_of_date):
        return [
            {
                "symbol": "RELIANCE",
                "sector": None,
                "listing_status": "ACTIVE",
                "close": 500.0,
                "avg_turnover_20d": 25.0,
            }
        ]

    monkeypatch.setattr("app.api.universe.compute_universe", fake_get_universe)

    resp = client.get("/api/universe")

    assert resp.status_code == 200
    assert resp.json()[0]["sector"] is None


def test_get_universe_empty_entries_below_liquidity_floor(
    client: TestClient, db_session: Session
) -> None:
    """A stock present in daily_ohlcv but below the liquidity floor should
    surface as a 200 empty array (get_universe's own filtering), not an error.
    """
    stock = make_stock(db_session, symbol="PENNY")
    make_daily_bar(db_session, stock, date(2026, 8, 10), close=5.0, volume=1000)

    resp = client.get("/api/universe")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_universe_explicit_as_of_date_is_honored(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing an explicit as_of_date must be threaded through to
    get_universe rather than silently defaulting to the latest date present.
    """
    make_stock(db_session, symbol="RELIANCE")

    captured: dict[str, date | None] = {}

    def fake_get_universe(session, as_of_date):
        captured["as_of_date"] = as_of_date
        return []

    monkeypatch.setattr("app.api.universe.compute_universe", fake_get_universe)

    resp = client.get("/api/universe", params={"as_of_date": "2026-01-01"})

    assert resp.status_code == 200
    assert captured["as_of_date"] == date(2026, 1, 1)


def test_get_universe_defaults_as_of_date_to_none_when_not_passed(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the query param is omitted, the route must pass as_of_date=None
    through to get_universe (which then resolves "latest") rather than
    resolving a date itself — that resolution is get_universe's job."""
    make_stock(db_session, symbol="RELIANCE")

    captured: dict[str, date | None] = {}

    def fake_get_universe(session, as_of_date):
        captured["as_of_date"] = as_of_date
        return []

    monkeypatch.setattr("app.api.universe.compute_universe", fake_get_universe)

    resp = client.get("/api/universe")

    assert resp.status_code == 200
    assert captured["as_of_date"] is None
