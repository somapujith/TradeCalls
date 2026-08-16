"""Pydantic response models for GET /api/news — see docs/api.md."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NewsArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str | None
    headline: str
    source: str
    url: str
    summary: str | None
    published_at: datetime
    event_type: str
    sentiment: str
    severity: int
    confidence: float
