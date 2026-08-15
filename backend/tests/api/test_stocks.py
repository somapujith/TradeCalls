"""Tests for GET /api/stocks/{symbol}/indicators — see docs/api.md.

Key contract points under test:
- start_date and end_date are both required query params (422 if missing).
- 404 if `symbol` isn't in `stocks`.
- Empty array (not 404) if the symbol exists but has no indicator rows in
  the requested range.
- Response is one row per date, all indicator fields nullable individually.
- symbol lookup is case-insensitive (stored/queried uppercased).
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import make_indicator, make_stock


def test_get_indicators_404_when_symbol_unknown(client: TestClient) -> None:
    resp = client.get(
        "/api/stocks/NOTREAL/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)


def test_get_indicators_empty_array_when_symbol_known_but_no_rows(
    client: TestClient, db_session: Session
) -> None:
    make_stock(db_session, symbol="RELIANCE")

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_indicators_happy_path(client: TestClient, db_session: Session) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(db_session, stock, date(2026, 1, 5), ema9=101.1, rsi=60.0)

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["date"] == "2026-01-05"
    assert row["ema9"] == 101.1
    assert row["rsi"] == 60.0
    assert set(row.keys()) == {
        "date",
        "ema9",
        "ema20",
        "ema50",
        "sma100",
        "sma200",
        "rsi",
        "macd",
        "macd_signal",
        "atr",
        "bb_upper",
        "bb_lower",
    }


def test_get_indicators_filters_by_date_range(client: TestClient, db_session: Session) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(db_session, stock, date(2025, 12, 31))
    make_indicator(db_session, stock, date(2026, 1, 15))
    make_indicator(db_session, stock, date(2026, 2, 1))

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["date"] == "2026-01-15"


def test_get_indicators_date_range_is_inclusive(client: TestClient, db_session: Session) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(db_session, stock, date(2026, 1, 1))
    make_indicator(db_session, stock, date(2026, 1, 31))

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    dates = {row["date"] for row in resp.json()}
    assert dates == {"2026-01-01", "2026-01-31"}


def test_get_indicators_ordered_ascending_by_date(client: TestClient, db_session: Session) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(db_session, stock, date(2026, 1, 20))
    make_indicator(db_session, stock, date(2026, 1, 5))
    make_indicator(db_session, stock, date(2026, 1, 12))

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    dates = [row["date"] for row in resp.json()]
    assert dates == sorted(dates)


def test_get_indicators_missing_start_date_422(client: TestClient, db_session: Session) -> None:
    make_stock(db_session, symbol="RELIANCE")

    resp = client.get("/api/stocks/RELIANCE/indicators", params={"end_date": "2026-01-31"})

    assert resp.status_code == 422


def test_get_indicators_missing_end_date_422(client: TestClient, db_session: Session) -> None:
    make_stock(db_session, symbol="RELIANCE")

    resp = client.get("/api/stocks/RELIANCE/indicators", params={"start_date": "2026-01-01"})

    assert resp.status_code == 422


def test_get_indicators_missing_both_dates_422(client: TestClient, db_session: Session) -> None:
    make_stock(db_session, symbol="RELIANCE")

    resp = client.get("/api/stocks/RELIANCE/indicators")

    assert resp.status_code == 422


def test_get_indicators_malformed_date_422(client: TestClient, db_session: Session) -> None:
    make_stock(db_session, symbol="RELIANCE")

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "not-a-date", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 422


def test_get_indicators_symbol_lookup_is_case_insensitive(client: TestClient, db_session: Session) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(db_session, stock, date(2026, 1, 5))

    resp = client.get(
        "/api/stocks/reliance/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_indicators_nullable_fields_pass_through_as_null(client: TestClient, db_session: Session) -> None:
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(
        db_session,
        stock,
        date(2026, 1, 5),
        ema9=None,
        ema20=None,
        ema50=None,
        sma100=None,
        sma200=None,
        rsi=None,
        macd=None,
        macd_signal=None,
        atr=None,
        bb_upper=None,
        bb_lower=None,
    )

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    row = resp.json()[0]
    for field in ["ema9", "ema20", "ema50", "sma100", "sma200", "rsi", "macd", "macd_signal", "atr", "bb_upper", "bb_lower"]:
        assert row[field] is None


def test_get_indicators_does_not_leak_other_symbols(client: TestClient, db_session: Session) -> None:
    stock_a = make_stock(db_session, symbol="RELIANCE")
    stock_b = make_stock(db_session, symbol="TCS")
    make_indicator(db_session, stock_a, date(2026, 1, 5))
    make_indicator(db_session, stock_b, date(2026, 1, 5))

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_indicators_end_before_start_returns_empty_not_error(
    client: TestClient, db_session: Session
) -> None:
    """An inverted range (end_date < start_date) isn't explicitly validated
    by the route — it simply yields no matching rows rather than erroring."""
    stock = make_stock(db_session, symbol="RELIANCE")
    make_indicator(db_session, stock, date(2026, 1, 15))

    resp = client.get(
        "/api/stocks/RELIANCE/indicators",
        params={"start_date": "2026-01-31", "end_date": "2026-01-01"},
    )

    assert resp.status_code == 200
    assert resp.json() == []
