"""Tests for app.notifications.telegram — no real network calls."""
from __future__ import annotations

import pytest

from app.notifications import telegram
from app.notifications.telegram import (
    TelegramNotConfiguredError,
    format_new_high_update,
    format_target_hit_update,
    format_trade_setup_alert,
    send_message,
    send_trade_setup_alert,
)


@pytest.fixture(autouse=True)
def _blank_config(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "")
    monkeypatch.setattr(telegram.settings, "telegram_chat_id", "")
    yield


def test_send_message_raises_not_configured_without_credentials():
    with pytest.raises(TelegramNotConfiguredError):
        send_message("hi")


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._payload


def test_send_message_posts_to_telegram_api(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "TOKEN")
    monkeypatch.setattr(telegram.settings, "telegram_chat_id", "12345")

    captured = {}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(telegram.requests, "post", _fake_post)

    send_message("hello")

    assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert captured["json"]["chat_id"] == "12345"
    assert captured["json"]["text"] == "hello"


def test_send_message_raises_when_telegram_rejects(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "TOKEN")
    monkeypatch.setattr(telegram.settings, "telegram_chat_id", "12345")
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout: _FakeResponse({"ok": False, "description": "bad"}))

    with pytest.raises(RuntimeError, match="rejected"):
        send_message("hello")


def test_format_trade_setup_alert_includes_key_fields():
    text = format_trade_setup_alert(
        symbol="LODHA",
        setup_type="BREAKOUT",
        entry_price=1245.0,
        stop_loss=1225.0,
        target_1r=1265.0,
        target_1_5r=1275.0,
        target_2r=1285.0,
        target_3r=1305.0,
        nearest_structural_target=1250.0,
        score=88.0,
        tier="A",
    )

    assert "LODHA" in text
    assert "POSITIONAL RESEARCH" in text
    assert "Looks good above ₹1,245.00" in text
    assert "SL ₹1,225.00" in text
    assert "88/100 (A)" in text


def test_format_trade_setup_alert_includes_event_caution_when_present():
    text = format_trade_setup_alert(
        symbol="INFY",
        setup_type="DIP_BUY",
        entry_price=None,
        stop_loss=1150.0,
        target_1r=1200.0,
        target_1_5r=1210.0,
        target_2r=1220.0,
        target_3r=1240.0,
        nearest_structural_target=None,
        score=72.0,
        tier="B",
        event_caution="Earnings due within 3 trading days",
    )

    assert "INFY" in text
    assert "Looks good above current level" in text  # no entry_price, no nearest_structural_target
    assert "Earnings due within 3 trading days" in text


def test_format_new_high_update_default_rockets():
    text = format_new_high_update("LODHA", 1252.40)

    assert "LODHA" in text
    assert "1,252.40" in text
    assert "made a high of" in text
    assert text.count("🚀") == 3


def test_format_new_high_update_custom_rocket_count():
    text = format_new_high_update("BDL", 1388.40, rockets=2)

    assert text.count("🚀") == 2


def test_format_target_hit_update_includes_entry_and_hit_price():
    text = format_target_hit_update("ELGI EQUIPMENT", entry_price=610.0, hit_price=619.0)

    assert "ELGI EQUIPMENT" in text
    assert "610.00" in text
    assert "619.00" in text
    assert "🎯🎯" in text


def test_send_trade_setup_alert_never_raises_on_missing_config():
    result = send_trade_setup_alert(
        symbol="TCS",
        setup_type="BREAKOUT",
        entry_price=100.0,
        stop_loss=95.0,
        target_1r=105.0,
        target_1_5r=107.5,
        target_2r=110.0,
        target_3r=115.0,
        nearest_structural_target=None,
        score=80.0,
        tier="A",
    )

    assert result is False


def test_send_trade_setup_alert_returns_true_on_success(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "TOKEN")
    monkeypatch.setattr(telegram.settings, "telegram_chat_id", "12345")
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout: _FakeResponse({"ok": True}))

    result = send_trade_setup_alert(
        symbol="TCS",
        setup_type="BREAKOUT",
        entry_price=100.0,
        stop_loss=95.0,
        target_1r=105.0,
        target_1_5r=107.5,
        target_2r=110.0,
        target_3r=115.0,
        nearest_structural_target=None,
        score=80.0,
        tier="A",
    )

    assert result is True
