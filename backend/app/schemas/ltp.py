"""Pydantic response models for /api/ltp/{symbol} — see docs/api.md."""
from __future__ import annotations

from pydantic import BaseModel


class LtpResponse(BaseModel):
    symbol: str
    price: float
    timestamp: float
