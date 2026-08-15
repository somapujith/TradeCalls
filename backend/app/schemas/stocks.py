"""Pydantic response models for /api/stocks/{symbol}/indicators — see docs/api.md."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class IndicatorPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    ema9: float | None
    ema20: float | None
    ema50: float | None
    sma100: float | None
    sma200: float | None
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    atr: float | None
    bb_upper: float | None
    bb_lower: float | None
