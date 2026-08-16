"""GET /api/ltp/{symbol} — see docs/api.md."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.schemas.ltp import LtpResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ltp", tags=["ltp"])


def _fetch_ltp(symbol: str) -> LtpResponse:
    """Calls angel_one_client.get_ltp(symbol), which raises
    AngelOneNotConfiguredError/AngelOneRequestError on failure and returns a
    {"price": float, "timestamp": float} dict on success (see
    app/data/angel_one_client.py) — symbol isn't part of that dict since the
    client is keyed by symbol per-call, so it's threaded through here.

    Angel One's ltpData needs a numeric symbol_token alongside the trading
    symbol, and there's no scrip-master lookup wired in yet (see
    angel_one_client's module docstring) — until that exists, this always
    raises AngelOneRequestError via the client's own missing-token check,
    which the route below correctly surfaces as 502 (lookup unavailable),
    not a symbol-doesn't-exist error.
    """
    from app.data import angel_one_client as angel_one_module

    quote = angel_one_module.get_ltp(symbol)
    return LtpResponse(symbol=symbol, price=quote["price"], timestamp=quote["timestamp"])


@router.get("/{symbol}", response_model=LtpResponse)
def get_ltp(symbol: str) -> LtpResponse:
    try:
        return _fetch_ltp(symbol.upper())
    except Exception as exc:
        logger.warning("LTP lookup failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail="Live price lookup unavailable") from exc
