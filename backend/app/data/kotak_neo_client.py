"""Kotak Neo live LTP client — display-only overlay, never feeds the engines.

See docs/engine.md#live-data-kotak-neo. This module wraps neo_api_client for
login/session handling and current-price lookup. Credentials
(kotak_neo_consumer_key/consumer_secret/mobile_number/password/mpin) are
read only from app.config.settings, never hardcoded, and are blank
placeholders until the user fills in .env — the module must import cleanly
in that state and only raise at call-time, when a caller actually needs a
live price.

neo_api_client's PyPI/git distribution is unreachable as of this writing
(see backend/requirements.txt) — the import is deferred into
_ensure_session so this module (and everything that imports it, e.g.
FastAPI startup) still loads fine without the package installed.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

_LTP_CACHE: dict[str, "LtpQuote"] = {}


@dataclass(frozen=True)
class LtpQuote:
    price: float
    timestamp: float  # unix epoch seconds


class KotakNeoNotConfiguredError(RuntimeError):
    """Raised when kotak_neo_* settings are blank — no credentials to log in with."""


class KotakNeoRequestError(RuntimeError):
    """Raised when login or the quote request itself fails against a configured account."""


class _SessionHolder:
    """Lazily-initialized, module-level Kotak Neo SDK session.

    A single shared session avoids re-logging-in on every get_ltp call;
    neo_api_client isn't imported until this actually runs.
    """

    def __init__(self) -> None:
        self._client = None

    def _configured(self) -> bool:
        return bool(
            settings.kotak_neo_consumer_key
            and settings.kotak_neo_consumer_secret
            and settings.kotak_neo_mobile_number
            and settings.kotak_neo_password
            and settings.kotak_neo_mpin
        )

    def get_client(self):
        if self._client is not None:
            return self._client

        if not self._configured():
            raise KotakNeoNotConfiguredError(
                "Kotak Neo credentials not configured — set kotak_neo_* in backend/.env"
            )

        try:
            from neo_api_client import NeoAPI  # deferred: optional dependency, see module docstring
        except ImportError as exc:
            raise KotakNeoRequestError(
                "neo_api_client is not installed — see backend/requirements.txt"
            ) from exc

        try:
            client = NeoAPI(
                consumer_key=settings.kotak_neo_consumer_key,
                consumer_secret=settings.kotak_neo_consumer_secret,
                environment="prod",
            )
            client.login(
                mobilenumber=settings.kotak_neo_mobile_number,
                password=settings.kotak_neo_password,
            )
            client.session_2fa(OTP=settings.kotak_neo_mpin)
        except Exception as exc:  # auth flow shape not fully confirmed publicly, per engine.md
            raise KotakNeoRequestError(f"Kotak Neo login failed: {exc}") from exc

        self._client = client
        return self._client


_session = _SessionHolder()


def get_ltp(symbol: str) -> dict:
    """Return {"price": float, "timestamp": float} for symbol.

    Raises KotakNeoNotConfiguredError or KotakNeoRequestError on any failure
    (blank creds, login failure, network error, bad response) — callers that
    must surface a clear failure (e.g. GET /api/ltp/{symbol} -> 502 per
    docs/api.md) should use this directly. Callers that must not be blocked
    by a flaky live lookup (e.g. GET /api/calls) should use get_ltp_safe.

    Cached per-symbol for settings.ltp_cache_ttl_seconds.
    """
    now = time.time()
    cached = _LTP_CACHE.get(symbol)
    if cached is not None and (now - cached.timestamp) < settings.ltp_cache_ttl_seconds:
        return {"price": cached.price, "timestamp": cached.timestamp}

    client = _session.get_client()

    try:
        response = client.quotes(
            instrument_tokens=[{"instrument_token": symbol, "exchange_segment": "nse_cm"}]
        )
        price = float(response["data"][0]["ltp"])
    except Exception as exc:
        raise KotakNeoRequestError(f"Kotak Neo LTP fetch failed for {symbol}: {exc}") from exc

    quote = LtpQuote(price=price, timestamp=now)
    _LTP_CACHE[symbol] = quote
    return {"price": quote.price, "timestamp": quote.timestamp}


def get_ltp_safe(symbol: str) -> dict | None:
    """Same as get_ltp but never raises — returns None on any failure.

    For callers that must not be blocked by a stale/missing LTP (docs/api.md:
    "a stale/missing LTP must not block the call from showing").
    """
    try:
        return get_ltp(symbol)
    except Exception as exc:
        logger.warning("get_ltp_safe: LTP unavailable for %s: %s", symbol, exc)
        return None
