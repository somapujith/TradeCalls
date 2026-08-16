"""Tests for GET /api/ltp/{symbol} — see docs/api.md.

Key contract points under test:
- Response: symbol, price, timestamp.
- 502 (not 404) when the Angel One lookup fails — the symbol may be valid,
  the live lookup is what's unavailable. Frontend must treat this as
  "LTP unavailable," not "symbol doesn't exist."
- app/api/ltp.py calls `angel_one_client.get_ltp(symbol)` expecting a
  {"price": float, "timestamp": float} dict on success, and treats any
  exception (AngelOneNotConfiguredError, AngelOneRequestError, or anything
  else) as a 502 — `symbol` itself comes from the path param, not the dict.
- No DB dependency — this route is pure passthrough to the live client.
- These tests monkeypatch `app.data.angel_one_client.get_ltp` wholesale, so
  the fakes below intentionally ignore the real implementation's
  symbol_token requirement (see angel_one_client.get_ltp's docstring) —
  the route only ever calls get_ltp(symbol) with no token, and the fakes
  stand in for the whole function, not a passthrough to the real one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    """No DB involvement in this route — plain TestClient, no lifespan
    (constructed without `with`, so app.main's real-DB create_all + scheduler
    start never run)."""
    return TestClient(app, raise_server_exceptions=False)


def test_get_ltp_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_ltp(symbol: str) -> dict:
        return {"price": 1234.5, "timestamp": 1_700_000_000.0}

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/RELIANCE")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert body["price"] == 1234.5
    assert body["timestamp"] == 1_700_000_000.0


def test_get_ltp_uppercases_symbol(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_get_ltp(symbol: str) -> dict:
        captured["symbol"] = symbol
        return {"price": 100.0, "timestamp": 1.0}

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/reliance")

    assert resp.status_code == 200
    assert captured["symbol"] == "RELIANCE"
    assert resp.json()["symbol"] == "RELIANCE"


def test_get_ltp_502_when_client_raises_request_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """502, not 404 — per docs/api.md: symbol may be valid, live lookup
    unavailable."""
    from app.data.angel_one_client import AngelOneRequestError

    def fake_get_ltp(symbol: str):
        raise AngelOneRequestError(f"Angel One LTP fetch failed for {symbol}: timeout")

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/RELIANCE")

    assert resp.status_code == 502
    assert resp.status_code != 404
    body = resp.json()
    assert set(body.keys()) == {"detail"}
    assert isinstance(body["detail"], str)


def test_get_ltp_502_when_not_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank Angel One credentials (AngelOneNotConfiguredError) must also
    surface as 502, same as any other live-lookup failure."""
    from app.data.angel_one_client import AngelOneNotConfiguredError

    def fake_get_ltp(symbol: str):
        raise AngelOneNotConfiguredError("Angel One credentials not configured")

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/RELIANCE")

    assert resp.status_code == 502


def test_get_ltp_502_when_client_raises_generic_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get_ltp(symbol: str):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/RELIANCE")

    assert resp.status_code == 502


def test_get_ltp_502_error_body_does_not_leak_internal_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get_ltp(symbol: str):
        raise RuntimeError("super secret internal stack trace / password=hunter2")

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/RELIANCE")

    assert resp.status_code == 502
    assert "hunter2" not in resp.text
    assert "Traceback" not in resp.text


def test_get_ltp_502_for_unknown_symbol_still_502_not_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a nonsense symbol should surface as 502 (live lookup failed), not
    404 — this endpoint doesn't validate against a stocks table."""

    def fake_get_ltp(symbol: str):
        raise RuntimeError(f"no such instrument: {symbol}")

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/NOTAREALSYMBOL")

    assert resp.status_code == 502


def test_get_ltp_missing_price_key_in_response_surfaces_as_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed/partial dict from the client (e.g. missing 'price') must
    not bubble up as an unhandled 500 — the route's try/except wraps
    _fetch_ltp entirely, so a KeyError here should still become a 502."""

    def fake_get_ltp(symbol: str) -> dict:
        return {"timestamp": 1.0}  # missing "price"

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/RELIANCE")

    assert resp.status_code == 502


def test_get_ltp_symbol_with_special_characters_url_encoded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get_ltp(symbol: str) -> dict:
        return {"price": 10.0, "timestamp": 1.0}

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/M%26M")  # M&M, URL-encoded

    assert resp.status_code == 200
    assert resp.json()["symbol"] == "M&M"


def test_get_ltp_response_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_ltp(symbol: str) -> dict:
        return {"price": 55.5, "timestamp": 42.0}

    monkeypatch.setattr("app.data.angel_one_client.get_ltp", fake_get_ltp)

    resp = client.get("/api/ltp/INFY")

    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"symbol", "price", "timestamp"}
