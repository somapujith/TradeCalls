"""Tests for app.data.angel_one_scrip_master — no network calls, the fetch
is monkeypatched at the requests layer."""
from __future__ import annotations

import pytest

from app.data import angel_one_scrip_master
from app.data.angel_one_scrip_master import (
    ScripMasterUnavailableError,
    resolve_equity_token,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    angel_one_scrip_master.reset_cache_for_tests()
    yield
    angel_one_scrip_master.reset_cache_for_tests()


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_resolve_equity_token_finds_nse_cash_equity(monkeypatch):
    payload = [
        {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "exch_seg": "NSE", "instrumenttype": ""},
        {"token": "9999", "symbol": "RELIANCE28AUGFUT", "name": "RELIANCE", "exch_seg": "NFO", "instrumenttype": "FUTSTK"},
    ]
    monkeypatch.setattr(angel_one_scrip_master.requests, "get", lambda url, timeout: _FakeResponse(payload))

    assert resolve_equity_token("RELIANCE") == "2885"


def test_resolve_equity_token_ignores_non_nse_or_non_eq_rows(monkeypatch):
    payload = [
        {"token": "1", "symbol": "RELIANCEFUT", "name": "RELIANCE", "exch_seg": "NFO", "instrumenttype": "FUTSTK"},
        {"token": "2", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "exch_seg": "BSE", "instrumenttype": ""},
    ]
    monkeypatch.setattr(angel_one_scrip_master.requests, "get", lambda url, timeout: _FakeResponse(payload))

    assert resolve_equity_token("RELIANCE") is None


def test_resolve_equity_token_returns_none_for_unknown_symbol(monkeypatch):
    monkeypatch.setattr(angel_one_scrip_master.requests, "get", lambda url, timeout: _FakeResponse([]))

    assert resolve_equity_token("NOPE") is None


def test_load_is_cached_across_calls(monkeypatch):
    calls = {"n": 0}

    def _get(url, timeout):
        calls["n"] += 1
        return _FakeResponse([{"token": "1", "symbol": "TCS-EQ", "name": "TCS", "exch_seg": "NSE", "instrumenttype": ""}])

    monkeypatch.setattr(angel_one_scrip_master.requests, "get", _get)

    resolve_equity_token("TCS")
    resolve_equity_token("TCS")

    assert calls["n"] == 1


def test_fetch_failure_raises_scrip_master_unavailable_error(monkeypatch):
    def _boom(url, timeout):
        raise ConnectionError("network down")

    monkeypatch.setattr(angel_one_scrip_master.requests, "get", _boom)

    with pytest.raises(ScripMasterUnavailableError):
        resolve_equity_token("TCS")
