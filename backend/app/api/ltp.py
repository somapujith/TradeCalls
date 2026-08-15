"""GET /api/ltp/{symbol} — see docs/api.md."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.schemas.ltp import LtpResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ltp", tags=["ltp"])


def _fetch_ltp(symbol: str) -> LtpResponse:
    """Calls kotak_neo_client.get_ltp(symbol), which raises
    KotakNeoNotConfiguredError/KotakNeoRequestError on failure and returns a
    {"price": float, "timestamp": float} dict on success (see
    app/data/kotak_neo_client.py) — symbol isn't part of that dict since the
    client is keyed by symbol per-call, so it's threaded through here.
    """
    from app.data import kotak_neo_client as kotak_neo_module

    quote = kotak_neo_module.get_ltp(symbol)
    return LtpResponse(symbol=symbol, price=quote["price"], timestamp=quote["timestamp"])


@router.get("/{symbol}", response_model=LtpResponse)
def get_ltp(symbol: str) -> LtpResponse:
    try:
        return _fetch_ltp(symbol.upper())
    except Exception as exc:
        logger.warning("LTP lookup failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail="Live price lookup unavailable") from exc
