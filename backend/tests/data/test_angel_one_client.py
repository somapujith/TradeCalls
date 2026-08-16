"""Tests for app.data.angel_one_client.

Coverage: with no credentials configured (the real default state per
config.py), get_ltp raises AngelOneNotConfiguredError gracefully rather
than attempting any network call, and get_ltp_safe swallows that into
None. Also covers the per-symbol cache TTL logic via direct manipulation
of the module's plain-dict cache and monkeypatched time.time — no network,
no real Angel One session ever constructed.

Also covers the symbol_token requirement specific to Angel One's ltpData
call: a cache miss with symbol_token=None raises AngelOneRequestError
(checked before the session is ever touched), while a cache hit returns
early and never even looks at symbol_token — see angel_one_client.get_ltp's
docstring and its source ordering (cache check, then symbol_token check,
then _session.get_client()).
"""
from __future__ import annotations

import pytest

from app.data import angel_one_client
from app.data.angel_one_client import (
    AngelOneNotConfiguredError,
    AngelOneRequestError,
    LtpQuote,
    _SessionHolder,
    get_ltp,
    get_ltp_safe,
)


@pytest.fixture(autouse=True)
def _isolated_cache_and_session(monkeypatch):
    """Every test gets a fresh cache and a fresh, unconfigured session
    holder so tests can't leak state into each other or accidentally reuse
    a previously-constructed SDK client.

    Credentials are explicitly blanked here too: config.py's field
    defaults are blank, but a developer's local backend/.env may have real
    angel_one_* values configured (this repo's own .env does, for the live
    LTP feature) — these tests must exercise the not-configured path
    deterministically regardless of what's in the environment running
    them, so blank the settings object directly rather than relying on
    defaults."""
    monkeypatch.setattr(angel_one_client, "_LTP_CACHE", {})
    monkeypatch.setattr(angel_one_client, "_session", _SessionHolder())
    monkeypatch.setattr(angel_one_client.settings, "angel_one_api_key", "")
    monkeypatch.setattr(angel_one_client.settings, "angel_one_client_code", "")
    monkeypatch.setattr(angel_one_client.settings, "angel_one_mpin", "")
    monkeypatch.setattr(angel_one_client.settings, "angel_one_totp_secret", "")
    yield


def test_no_credentials_configured_is_the_real_default_state():
    """config.py's field defaults are blank (verified here on a fresh
    Settings() instance, independent of any local .env override the
    autouse fixture already blanks on the shared `settings` singleton)."""
    from app.config import Settings

    defaults = Settings(_env_file=None)
    assert defaults.angel_one_api_key == ""
    assert defaults.angel_one_client_code == ""
    assert defaults.angel_one_mpin == ""
    assert defaults.angel_one_totp_secret == ""


def test_get_ltp_raises_not_configured_error_without_credentials():
    """Without credentials, the not-configured check must win even though a
    symbol_token is also missing — get_ltp needs a valid symbol_token to
    reach the session at all, so pass one here to prove the failure really
    comes from _configured(), not the symbol_token guard."""
    with pytest.raises(AngelOneNotConfiguredError):
        get_ltp("TCS-EQ", symbol_token="3045")


def test_get_ltp_does_not_attempt_network_call_without_credentials(monkeypatch):
    """Guard: if credentials are blank, _SessionHolder.get_client must raise
    AngelOneNotConfiguredError before ever reaching the smartapi-python
    import/login path (which is deferred exactly for this reason — see
    module docstring). Verify by asserting the client construction never
    completes (no client cached on the session) rather than an SDK call
    ever occurring."""
    holder = angel_one_client._session

    with pytest.raises(AngelOneNotConfiguredError):
        get_ltp("TCS-EQ", symbol_token="3045")

    assert holder._client is None  # no SDK session was ever constructed


def test_get_ltp_safe_returns_none_without_credentials_and_does_not_raise():
    result = get_ltp_safe("TCS-EQ", symbol_token="3045")

    assert result is None


def test_get_ltp_safe_never_raises_even_on_unexpected_error(monkeypatch):
    def _raise_value_error(symbol, exchange="NSE", symbol_token=None):
        raise ValueError("unexpected")

    monkeypatch.setattr(angel_one_client, "get_ltp", _raise_value_error)

    result = get_ltp_safe("TCS-EQ", symbol_token="3045")

    assert result is None


# --- symbol_token requirement ---


def test_get_ltp_raises_request_error_when_symbol_token_is_none_on_cache_miss(monkeypatch):
    """A cache miss with no symbol_token must raise AngelOneRequestError —
    Angel One's ltpData needs the numeric instrument token and there's no
    scrip-master lookup wired in yet (see module docstring). This must be
    true even with otherwise valid-looking state (nothing cached, no
    credentials needed to reach this check since it happens before
    _session.get_client() is ever called)."""
    with pytest.raises(AngelOneRequestError):
        get_ltp("TCS-EQ", symbol_token=None)


def test_get_ltp_symbol_token_none_does_not_touch_session(monkeypatch):
    """The symbol_token check happens before _session.get_client() — assert
    no session/client construction is attempted when symbol_token is
    missing, confirming the guard fires first rather than falling through
    to a (would-be) not-configured error."""
    holder = angel_one_client._session

    def _boom():
        raise AssertionError("must not construct a session when symbol_token is None")

    monkeypatch.setattr(holder, "get_client", _boom)

    with pytest.raises(AngelOneRequestError):
        get_ltp("TCS-EQ", symbol_token=None)


def test_get_ltp_cache_hit_does_not_require_symbol_token(monkeypatch):
    """Per get_ltp's source, the cache check happens first and returns early
    — a cache hit must succeed even with symbol_token=None, since the
    symbol_token check is never reached."""
    monkeypatch.setattr(angel_one_client.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(angel_one_client.settings, "ltp_cache_ttl_seconds", 60)
    angel_one_client._LTP_CACHE["TCS-EQ"] = LtpQuote(price=321.0, timestamp=999.0)

    result = get_ltp("TCS-EQ", symbol_token=None)

    assert result == {"price": 321.0, "timestamp": 999.0}


# --- Cache TTL logic ---


def test_cache_hit_within_ttl_returns_cached_value_without_calling_session(monkeypatch):
    monkeypatch.setattr(angel_one_client.time, "time", lambda: 1_000.0)
    angel_one_client._LTP_CACHE["TCS-EQ"] = LtpQuote(price=123.45, timestamp=990.0)  # 10s old
    monkeypatch.setattr(angel_one_client.settings, "ltp_cache_ttl_seconds", 60)

    def _boom():
        raise AssertionError("must not construct a session on a cache hit")

    monkeypatch.setattr(angel_one_client._session, "get_client", _boom)

    result = get_ltp("TCS-EQ", symbol_token="3045")

    assert result == {"price": 123.45, "timestamp": 990.0}


def test_cache_miss_when_entry_expired_falls_through_to_session_lookup(monkeypatch):
    monkeypatch.setattr(angel_one_client.time, "time", lambda: 1_000.0)
    angel_one_client._LTP_CACHE["TCS-EQ"] = LtpQuote(price=100.0, timestamp=900.0)  # 100s old, ttl=60
    monkeypatch.setattr(angel_one_client.settings, "ltp_cache_ttl_seconds", 60)

    # No credentials configured -> expired cache falls through to the
    # not-configured path (proves it did NOT short-circuit on the stale entry).
    with pytest.raises(AngelOneNotConfiguredError):
        get_ltp("TCS-EQ", symbol_token="3045")


def test_cache_is_isolated_per_symbol(monkeypatch):
    monkeypatch.setattr(angel_one_client.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(angel_one_client.settings, "ltp_cache_ttl_seconds", 60)
    angel_one_client._LTP_CACHE["TCS-EQ"] = LtpQuote(price=200.0, timestamp=995.0)

    result = get_ltp("TCS-EQ", symbol_token="3045")

    assert result["price"] == pytest.approx(200.0)
    # a different, uncached symbol must not reuse TCS-EQ's cache entry
    with pytest.raises(AngelOneNotConfiguredError):
        get_ltp("INFY-EQ", symbol_token="1594")


def test_cache_entry_exactly_at_ttl_boundary_is_treated_as_expired(monkeypatch):
    """(now - cached.timestamp) < ttl is strict; exactly-equal must not hit."""
    monkeypatch.setattr(angel_one_client.time, "time", lambda: 1_060.0)
    monkeypatch.setattr(angel_one_client.settings, "ltp_cache_ttl_seconds", 60)
    angel_one_client._LTP_CACHE["TCS-EQ"] = LtpQuote(price=150.0, timestamp=1_000.0)  # exactly 60s old

    with pytest.raises(AngelOneNotConfiguredError):
        get_ltp("TCS-EQ", symbol_token="3045")


def test_get_ltp_safe_uses_cache_and_returns_dict_without_raising(monkeypatch):
    monkeypatch.setattr(angel_one_client.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(angel_one_client.settings, "ltp_cache_ttl_seconds", 60)
    angel_one_client._LTP_CACHE["TCS-EQ"] = LtpQuote(price=321.0, timestamp=999.0)

    result = get_ltp_safe("TCS-EQ", symbol_token="3045")

    assert result == {"price": 321.0, "timestamp": 999.0}


def test_session_holder_not_configured_without_any_credentials():
    holder = _SessionHolder()

    with pytest.raises(AngelOneNotConfiguredError):
        holder.get_client()


def test_session_holder_configured_check_requires_all_four_fields(monkeypatch):
    holder = _SessionHolder()
    monkeypatch.setattr(angel_one_client.settings, "angel_one_api_key", "key")
    monkeypatch.setattr(angel_one_client.settings, "angel_one_client_code", "")
    monkeypatch.setattr(angel_one_client.settings, "angel_one_mpin", "1234")
    monkeypatch.setattr(angel_one_client.settings, "angel_one_totp_secret", "secret")

    assert holder._configured() is False  # client_code still blank

    monkeypatch.setattr(angel_one_client.settings, "angel_one_client_code", "A123456")
    assert holder._configured() is True
