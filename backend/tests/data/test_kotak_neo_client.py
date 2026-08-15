"""Tests for app.data.kotak_neo_client.

Coverage: with no credentials configured (the real default state per
config.py), get_ltp raises KotakNeoNotConfiguredError gracefully rather
than attempting any network call, and get_ltp_safe swallows that into
None. Also covers the per-symbol cache TTL logic via direct manipulation
of the module's plain-dict cache and monkeypatched time.time — no network,
no real Kotak Neo session ever constructed.
"""
from __future__ import annotations

import pytest

from app.data import kotak_neo_client
from app.data.kotak_neo_client import (
    KotakNeoNotConfiguredError,
    LtpQuote,
    _SessionHolder,
    get_ltp,
    get_ltp_safe,
)


@pytest.fixture(autouse=True)
def _isolated_cache_and_session(monkeypatch):
    """Every test gets a fresh cache and a fresh, unconfigured session
    holder so tests can't leak state into each other or accidentally reuse
    a previously-constructed SDK client."""
    monkeypatch.setattr(kotak_neo_client, "_LTP_CACHE", {})
    monkeypatch.setattr(kotak_neo_client, "_session", _SessionHolder())
    yield


def test_no_credentials_configured_is_the_real_default_state():
    assert kotak_neo_client.settings.kotak_neo_consumer_key == ""
    assert kotak_neo_client.settings.kotak_neo_consumer_secret == ""
    assert kotak_neo_client.settings.kotak_neo_mobile_number == ""
    assert kotak_neo_client.settings.kotak_neo_password == ""
    assert kotak_neo_client.settings.kotak_neo_mpin == ""


def test_get_ltp_raises_not_configured_error_without_credentials():
    with pytest.raises(KotakNeoNotConfiguredError):
        get_ltp("TCS")


def test_get_ltp_does_not_attempt_network_call_without_credentials(monkeypatch):
    """Guard: if credentials are blank, _SessionHolder.get_client must raise
    KotakNeoNotConfiguredError before ever reaching the neo_api_client
    import/login path (which is deferred exactly for this reason — see
    module docstring). Verify by asserting the client construction never
    completes (no client cached on the session) rather than an SDK call
    ever occurring."""
    holder = kotak_neo_client._session

    with pytest.raises(KotakNeoNotConfiguredError):
        get_ltp("TCS")

    assert holder._client is None  # no SDK session was ever constructed


def test_get_ltp_safe_returns_none_without_credentials_and_does_not_raise():
    result = get_ltp_safe("TCS")

    assert result is None


def test_get_ltp_safe_never_raises_even_on_unexpected_error(monkeypatch):
    def _raise_value_error(symbol):
        raise ValueError("unexpected")

    monkeypatch.setattr(kotak_neo_client, "get_ltp", _raise_value_error)

    result = get_ltp_safe("TCS")

    assert result is None


# --- Cache TTL logic ---


def test_cache_hit_within_ttl_returns_cached_value_without_calling_session(monkeypatch):
    monkeypatch.setattr(kotak_neo_client.time, "time", lambda: 1_000.0)
    kotak_neo_client._LTP_CACHE["TCS"] = LtpQuote(price=123.45, timestamp=990.0)  # 10s old
    monkeypatch.setattr(kotak_neo_client.settings, "ltp_cache_ttl_seconds", 60)

    def _boom():
        raise AssertionError("must not construct a session on a cache hit")

    monkeypatch.setattr(kotak_neo_client._session, "get_client", _boom)

    result = get_ltp("TCS")

    assert result == {"price": 123.45, "timestamp": 990.0}


def test_cache_miss_when_entry_expired_falls_through_to_session_lookup(monkeypatch):
    monkeypatch.setattr(kotak_neo_client.time, "time", lambda: 1_000.0)
    kotak_neo_client._LTP_CACHE["TCS"] = LtpQuote(price=100.0, timestamp=900.0)  # 100s old, ttl=60
    monkeypatch.setattr(kotak_neo_client.settings, "ltp_cache_ttl_seconds", 60)

    # No credentials configured -> expired cache falls through to the
    # not-configured path (proves it did NOT short-circuit on the stale entry).
    with pytest.raises(KotakNeoNotConfiguredError):
        get_ltp("TCS")


def test_cache_is_isolated_per_symbol(monkeypatch):
    monkeypatch.setattr(kotak_neo_client.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(kotak_neo_client.settings, "ltp_cache_ttl_seconds", 60)
    kotak_neo_client._LTP_CACHE["TCS"] = LtpQuote(price=200.0, timestamp=995.0)

    result = get_ltp("TCS")

    assert result["price"] == pytest.approx(200.0)
    # a different, uncached symbol must not reuse TCS's cache entry
    with pytest.raises(KotakNeoNotConfiguredError):
        get_ltp("INFY")


def test_cache_entry_exactly_at_ttl_boundary_is_treated_as_expired(monkeypatch):
    """(now - cached.timestamp) < ttl is strict; exactly-equal must not hit."""
    monkeypatch.setattr(kotak_neo_client.time, "time", lambda: 1_060.0)
    monkeypatch.setattr(kotak_neo_client.settings, "ltp_cache_ttl_seconds", 60)
    kotak_neo_client._LTP_CACHE["TCS"] = LtpQuote(price=150.0, timestamp=1_000.0)  # exactly 60s old

    with pytest.raises(KotakNeoNotConfiguredError):
        get_ltp("TCS")


def test_get_ltp_safe_uses_cache_and_returns_dict_without_raising(monkeypatch):
    monkeypatch.setattr(kotak_neo_client.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(kotak_neo_client.settings, "ltp_cache_ttl_seconds", 60)
    kotak_neo_client._LTP_CACHE["TCS"] = LtpQuote(price=321.0, timestamp=999.0)

    result = get_ltp_safe("TCS")

    assert result == {"price": 321.0, "timestamp": 999.0}


def test_session_holder_not_configured_without_any_credentials():
    holder = _SessionHolder()

    with pytest.raises(KotakNeoNotConfiguredError):
        holder.get_client()


def test_session_holder_configured_check_requires_all_five_fields(monkeypatch):
    holder = _SessionHolder()
    monkeypatch.setattr(kotak_neo_client.settings, "kotak_neo_consumer_key", "key")
    monkeypatch.setattr(kotak_neo_client.settings, "kotak_neo_consumer_secret", "")
    monkeypatch.setattr(kotak_neo_client.settings, "kotak_neo_mobile_number", "9999999999")
    monkeypatch.setattr(kotak_neo_client.settings, "kotak_neo_password", "pw")
    monkeypatch.setattr(kotak_neo_client.settings, "kotak_neo_mpin", "1234")

    assert holder._configured() is False  # secret still blank

    monkeypatch.setattr(kotak_neo_client.settings, "kotak_neo_consumer_secret", "secret")
    assert holder._configured() is True
