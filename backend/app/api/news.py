"""GET /api/news — see docs/api.md. Backs the frontend News tab."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NewsArticle
from app.db.session import get_db
from app.schemas.news import NewsArticleOut

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("", response_model=list[NewsArticleOut])
def list_news(
    symbol: str | None = Query(default=None),
    min_severity: int = Query(default=0, ge=0, le=5),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[NewsArticle]:
    query = select(NewsArticle).where(NewsArticle.severity >= min_severity)
    if symbol is not None:
        query = query.where(NewsArticle.symbol == symbol.upper())
    query = query.order_by(NewsArticle.published_at.desc()).limit(limit)
    return list(db.scalars(query).all())
