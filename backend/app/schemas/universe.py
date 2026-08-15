"""Pydantic response models for /api/universe — see docs/api.md."""
from __future__ import annotations

from pydantic import BaseModel


class UniverseEntryOut(BaseModel):
    symbol: str
    sector: str | None
    listing_status: str
    close: float
    avg_turnover_20d: float
