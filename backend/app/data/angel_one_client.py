"""Angel One SmartAPI live LTP client — display-only overlay, never feeds the engines.

Replaces the earlier Kotak Neo integration (see docs/engine.md's live-data
section) — same free-tier, zero-budget constraint, same display-only role:
this shows current price next to a call that was generated from yesterday's
close (per the daily-bar engine), it does not change the call itself.

This module wraps smartapi-python's SmartConnect for login/session handling
and current-price lookup. Credentials (angel_one_api_key/client_code/
mpin/totp_secret) are read only from app.config.settings, never hardcoded,
and are blank placeholders until the user fills in .env — the module must
import cleanly in that state and only raise at call-time, when a caller
actually needs a live price.

Angel One's generateSession() takes three factors: client code, the
account's MPIN (passed in the SDK's "password" argument slot — MPIN-only
accounts don't have a separate longer password), and a TOTP generated from
a base32 secret the user gets once when enabling 2FA on the SmartAPI portal
(scan the same QR an authenticator app would use, but keep the underlying
secret instead of only the app). The `pyotp` package computes the current
TOTP code from that secret at login time.

smartapi-python's import is deferred into _ensure_session so this module
(and everything that imports it, e.g. FastAPI startup) still loads fine
without the package installed or before credentials are configured.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

_LTP_CACHE: dict[str, "LtpQuote"] = {}

# Angel One's quote API is keyed by exchange + symboltoken, not a bare NSE
# symbol — a full instrument-master lookup (their published scrip master
# JSON) is the correct v-next solution. For v1's display-only overlay, the
# exchange is assumed NSE cash-market ("NSE") and the symbol is passed
# through as tradingsymbol with an NSE_EQ suffix convention; callers must
# supply Angel One's tradingsymbol form (e.g. "RELIANCE-EQ"), not a bare
# NSE ticker, until the scrip-master lookup exists.
DEFAULT_EXCHANGE = "NSE"


@dataclass(frozen=True)
class LtpQuote:
    price: float
    timestamp: float  # unix epoch seconds


class AngelOneNotConfiguredError(RuntimeError):
    """Raised when angel_one_* settings are blank — no credentials to log in with."""


class AngelOneRequestError(RuntimeError):
    """Raised when login or the quote request itself fails against a configured account."""


class _SessionHolder:
    """Lazily-initialized, module-level Angel One SmartAPI session.

    A single shared session avoids re-logging-in on every get_ltp call;
    smartapi-python isn't imported until this actually runs.
    """

    def __init__(self) -> None:
        self._client = None

    def _configured(self) -> bool:
        return bool(
            settings.angel_one_api_key
            and settings.angel_one_client_code
            and settings.angel_one_mpin
            and settings.angel_one_totp_secret
        )

    def get_client(self):
        if self._client is not None:
            return self._client

        if not self._configured():
            raise AngelOneNotConfiguredError(
                "Angel One credentials not configured — set angel_one_* in backend/.env"
            )

        try:
            from SmartApi import SmartConnect  # deferred: optional dependency, see module docstring
        except ImportError as exc:
            raise AngelOneRequestError(
                "smartapi-python is not installed — see backend/requirements.txt"
            ) from exc

        try:
            import pyotp
        except ImportError as exc:
            raise AngelOneRequestError(
                "pyotp is not installed (needed for Angel One's TOTP login) — see backend/requirements.txt"
            ) from exc

        try:
            totp = pyotp.TOTP(settings.angel_one_totp_secret).now()
            client = SmartConnect(api_key=settings.angel_one_api_key)
            client.generateSession(
                settings.angel_one_client_code,
                settings.angel_one_mpin,
                totp,
            )
        except Exception as exc:  # auth flow details verified against smartapi-python's own docs at implementation time
            raise AngelOneRequestError(f"Angel One login failed: {exc}") from exc

        self._client = client
        return self._client


_session = _SessionHolder()


def get_ltp(symbol: str, exchange: str = DEFAULT_EXCHANGE, symbol_token: str | None = None) -> dict:
    """Return {"price": float, "timestamp": float} for symbol.

    symbol: Angel One tradingsymbol form (e.g. "RELIANCE-EQ"), not a bare NSE ticker.
    symbol_token: Angel One's numeric instrument token, required by their
    ltpData call. Without a scrip-master lookup wired in yet, callers must
    supply it explicitly — passing None raises AngelOneRequestError rather
    than guessing a token, since a wrong token would silently quote the
    wrong instrument.

    Raises AngelOneNotConfiguredError or AngelOneRequestError on any failure
    (blank creds, login failure, network error, bad response, missing
    symbol_token) — callers that must surface a clear failure (e.g.
    GET /api/ltp/{symbol} -> 502 per docs/api.md) should use this directly.
    Callers that must not be blocked by a flaky live lookup (e.g.
    GET /api/calls) should use get_ltp_safe.

    Cached per-symbol for settings.ltp_cache_ttl_seconds.
    """
    now = time.time()
    cached = _LTP_CACHE.get(symbol)
    if cached is not None and (now - cached.timestamp) < settings.ltp_cache_ttl_seconds:
        return {"price": cached.price, "timestamp": cached.timestamp}

    if symbol_token is None:
        raise AngelOneRequestError(
            f"symbol_token required for {symbol} — Angel One's ltpData needs the numeric "
            "instrument token from their scrip master, not just the trading symbol"
        )

    client = _session.get_client()

    try:
        response = client.ltpData(exchange, symbol, symbol_token)
        price = float(response["data"]["ltp"])
    except Exception as exc:
        raise AngelOneRequestError(f"Angel One LTP fetch failed for {symbol}: {exc}") from exc

    quote = LtpQuote(price=price, timestamp=now)
    _LTP_CACHE[symbol] = quote
    return {"price": quote.price, "timestamp": quote.timestamp}


def get_ltp_safe(symbol: str, exchange: str = DEFAULT_EXCHANGE, symbol_token: str | None = None) -> dict | None:
    """Same as get_ltp but never raises — returns None on any failure.

    For callers that must not be blocked by a stale/missing LTP (docs/api.md:
    "a stale/missing LTP must not block the call from showing").
    """
    try:
        return get_ltp(symbol, exchange=exchange, symbol_token=symbol_token)
    except Exception as exc:
        logger.warning("get_ltp_safe: LTP unavailable for %s: %s", symbol, exc)
        return None
