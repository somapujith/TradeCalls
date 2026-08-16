"""Tests for GET /api/calls — see docs/api.md's GET /api/calls section.

Key contract points under test:
- No strategy_version path param — always reflects the *latest* strategy_version
  present in trade_setups (by created_at), unlike the /api/backtests/* endpoints.
- Only non-terminal-state trade_setups are returned (TARGET_HIT/INVALIDATED/
  SESSION_END/NOT_CONFIRMED excluded per app.breakout.states.TERMINAL_STATES).
- `ltp` is nullable and a failed/stale Angel One lookup must NOT block the call
  from showing (still 200, ltp: null).
- `confidence` is the setup's deterministic `score`, not an ML output.
- `reason` is the parsed score_breakdown JSON (dict), or None if unparseable/null.
- Response is a bare JSON array, ordered by score descending.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import make_stock, make_trade_setup


def test_get_calls_empty_db_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/api/calls")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_calls_returns_non_terminal_setups_from_latest_version(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.data.angel_one_client.get_ltp_safe", lambda symbol: {"price": 501.5, "timestamp": 123.0}
    )

    stock = make_stock(db_session, symbol="RELIANCE")
    make_trade_setup(db_session, stock, strategy_version="v1", state="CONFIRMED", score=80.0)

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    call = body[0]
    assert call["symbol"] == "RELIANCE"
    assert call["setup_type"] == "BREAKOUT"
    assert call["state"] == "CONFIRMED"
    assert call["confidence"] == 80.0
    assert call["ltp"] == 501.5
    assert call["reason"] == {"rvol": 20, "trend": 15}
    assert call["targets"] == [530.0, 542.5, 555.0, 580.0, 560.0]


@pytest.mark.parametrize("terminal_state", ["TARGET_HIT", "INVALIDATED", "SESSION_END", "NOT_CONFIRMED"])
def test_get_calls_excludes_terminal_states(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, terminal_state: str
) -> None:
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="TCS")
    make_trade_setup(db_session, stock, strategy_version="v1", state=terminal_state)

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_calls_includes_non_terminal_states(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="INFY")
    for state in ["CONFIRMED", "RETEST_PENDING", "TRADE_ACTIVE"]:
        make_trade_setup(db_session, stock, strategy_version="v1", state=state, score=50.0)

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    states = {row["state"] for row in resp.json()}
    assert states == {"CONFIRMED", "RETEST_PENDING", "TRADE_ACTIVE"}


def test_get_calls_only_uses_latest_strategy_version(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older strategy_version rows must not leak into /api/calls even if they
    are non-terminal — the endpoint always reflects the current production run.
    """
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="HDFCBANK")
    old_time = datetime.utcnow() - timedelta(days=5)
    new_time = datetime.utcnow()

    make_trade_setup(
        db_session, stock, strategy_version="v-old", state="CONFIRMED", score=99.0, created_at=old_time
    )
    make_trade_setup(
        db_session, stock, strategy_version="v-new", state="CONFIRMED", score=10.0, created_at=new_time
    )

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["confidence"] == 10.0


def test_get_calls_ltp_null_when_angel_one_lookup_fails(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed/unavailable live LTP lookup must not block the call from
    showing — docs/api.md: 'a stale/missing LTP must not block the call from
    showing, since the call itself comes from yesterday's close, not from LTP.'
    """

    def _raise(symbol: str) -> dict | None:
        raise RuntimeError("Angel One session expired")

    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", _raise)

    stock = make_stock(db_session, symbol="WIPRO")
    make_trade_setup(db_session, stock, strategy_version="v1", state="CONFIRMED")

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ltp"] is None


def test_get_calls_ltp_null_when_angel_one_returns_none(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="ITC")
    make_trade_setup(db_session, stock, strategy_version="v1", state="CONFIRMED")

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    assert resp.json()[0]["ltp"] is None


def test_get_calls_ordered_by_score_descending(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock_a = make_stock(db_session, symbol="AAA")
    stock_b = make_stock(db_session, symbol="BBB")
    stock_c = make_stock(db_session, symbol="CCC")
    make_trade_setup(db_session, stock_a, strategy_version="v1", state="CONFIRMED", score=40.0)
    make_trade_setup(db_session, stock_b, strategy_version="v1", state="CONFIRMED", score=90.0)
    make_trade_setup(db_session, stock_c, strategy_version="v1", state="CONFIRMED", score=65.0)

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    scores = [row["confidence"] for row in resp.json()]
    assert scores == sorted(scores, reverse=True)


def test_get_calls_reason_null_when_score_breakdown_is_null(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="LT")
    make_trade_setup(db_session, stock, strategy_version="v1", state="CONFIRMED", score_breakdown=None)

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    assert resp.json()[0]["reason"] is None


def test_get_calls_reason_null_when_score_breakdown_is_invalid_json(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="AXISBANK")
    make_trade_setup(db_session, stock, strategy_version="v1", state="CONFIRMED", score_breakdown="not-json{")

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    assert resp.json()[0]["reason"] is None


def test_get_calls_entry_price_nullable(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entry_price is nullable per schema (a CONFIRMED setup may not have
    triggered an entry yet)."""
    monkeypatch.setattr("app.data.angel_one_client.get_ltp_safe", lambda symbol: None)

    stock = make_stock(db_session, symbol="SBIN")
    make_trade_setup(db_session, stock, strategy_version="v1", state="CONFIRMED", entry_price=None)

    resp = client.get("/api/calls")

    assert resp.status_code == 200
    assert resp.json()[0]["entry_price"] is None


def test_get_calls_does_not_take_strategy_version_path_param(client: TestClient) -> None:
    """/api/calls intentionally has no {strategy_version} path segment."""
    resp = client.get("/api/calls/v1")

    assert resp.status_code == 404
