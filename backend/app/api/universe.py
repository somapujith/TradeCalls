"""GET /api/universe — see docs/api.md."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.data.universe import get_universe as compute_universe
from app.db.session import get_db
from app.schemas.universe import UniverseEntryOut

router = APIRouter(prefix="/api/universe", tags=["universe"])


@router.get("", response_model=list[UniverseEntryOut])
def get_universe(
    as_of_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[UniverseEntryOut]:
    entries = compute_universe(db, as_of_date)
    return [UniverseEntryOut(**entry) for entry in entries]
