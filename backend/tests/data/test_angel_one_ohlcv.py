"""Tests for app.data.angel_one_ohlcv — no network/session calls, both the
token lookup and get_candle_data are monkeypatched."""
from __future__ import annotations

from datetime import date

import pytest

from app.data import angel_one_ohlcv
from app.data.angel_one_client import AngelOneRequestError
from app.data.angel_one_ohlcv import NIFTY_SYMBOL, fetch_daily_ohlcv


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(angel_one_ohlcv.time, "sleep", lambda seconds: None)


def test_fetch_daily_ohlcv_resolves_token_and_maps_rows(monkeypatch):
    monkeypatch.setattr(angel_one_ohlcv, "resolve_equity_token", lambda symbol: "2885")

    def _fake_get_candle_data(token, interval, from_date, to_date, exchange="NSE"):
        assert token == "2885"
        return [
            {"timestamp": "2026-08-10T00:00:00+05:30", "open": 1178.1, "high": 1195.0, "low": 1175.2, "close": 1183.0, "volume": 9377001},
        ]

    monkeypatch.setattr(angel_one_ohlcv, "get_candle_data", _fake_get_candle_data)

    result = fetch_daily_ohlcv("RELIANCE", date(2026, 8, 1), date(2026, 8, 10))

    assert len(result) == 1
    row = result.iloc[0]
    assert row["date"] == date(2026, 8, 10)
    assert row["close"] == 1183.0
    assert row["adjusted_close"] == 1183.0  # no split-adjustment from Angel One — see module docstring
    assert row["volume"] == 9377001


def test_fetch_daily_ohlcv_uses_nifty_index_token_for_nifty_symbol(monkeypatch):
    seen_tokens = []

    def _fake_get_candle_data(token, interval, from_date, to_date, exchange="NSE"):
        seen_tokens.append(token)
        return []

    monkeypatch.setattr(angel_one_ohlcv, "get_candle_data", _fake_get_candle_data)
    # resolve_equity_token must not even be consulted for the index symbol
    monkeypatch.setattr(angel_one_ohlcv, "resolve_equity_token", lambda symbol: (_ for _ in ()).throw(AssertionError("must not resolve NIFTY via equity lookup")))

    fetch_daily_ohlcv(NIFTY_SYMBOL, date(2026, 8, 1), date(2026, 8, 10))

    assert seen_tokens == ["99926000"]


def test_fetch_daily_ohlcv_raises_when_symbol_not_found(monkeypatch):
    monkeypatch.setattr(angel_one_ohlcv, "resolve_equity_token", lambda symbol: None)

    with pytest.raises(AngelOneRequestError, match="No Angel One scrip-master token"):
        fetch_daily_ohlcv("NOTASYMBOL", date(2026, 8, 1), date(2026, 8, 10))


def test_fetch_daily_ohlcv_empty_result_returns_empty_dataframe(monkeypatch):
    monkeypatch.setattr(angel_one_ohlcv, "resolve_equity_token", lambda symbol: "2885")
    monkeypatch.setattr(angel_one_ohlcv, "get_candle_data", lambda *a, **kw: [])

    result = fetch_daily_ohlcv("RELIANCE", date(2026, 8, 1), date(2026, 8, 10))

    assert result.empty
    assert list(result.columns) == ["date", "open", "high", "low", "close", "adjusted_close", "volume"]


def test_fetch_daily_ohlcv_sleeps_for_rate_limit(monkeypatch):
    slept = []
    monkeypatch.setattr(angel_one_ohlcv.time, "sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(angel_one_ohlcv, "resolve_equity_token", lambda symbol: "2885")
    monkeypatch.setattr(angel_one_ohlcv, "get_candle_data", lambda *a, **kw: [])
    monkeypatch.setattr(angel_one_ohlcv.settings, "angel_one_candle_request_delay_seconds", 0.4)

    fetch_daily_ohlcv("RELIANCE", date(2026, 8, 1), date(2026, 8, 10))

    assert slept == [0.4]
