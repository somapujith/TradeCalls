"""GET /api/calls — see docs/api.md."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import parse_json_field, targets_to_list
from app.db.models import Stock, TradeSetup
from app.db.session import get_db
from app.schemas.calls import ActiveCall

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calls", tags=["calls"])


def _terminal_states() -> set[str]:
    try:
        from app.breakout.states import TERMINAL_STATES

        return set(TERMINAL_STATES)
    except ImportError:
        return {"TARGET_HIT", "INVALIDATED", "SESSION_END"}


def _fetch_ltp_safe(symbol: str) -> float | None:
    try:
        from app.data import kotak_neo_client as kotak_neo_module

        quote = kotak_neo_module.get_ltp_safe(symbol)
        return quote["price"] if quote is not None else None
    except Exception as exc:
        logger.warning("LTP lookup failed for %s in /api/calls: %s", symbol, exc)
        return None


@router.get("", response_model=list[ActiveCall])
def get_active_calls(db: Session = Depends(get_db)) -> list[ActiveCall]:
    latest_version_row = (
        db.query(TradeSetup.strategy_version)
        .order_by(TradeSetup.created_at.desc())
        .first()
    )
    if latest_version_row is None:
        return []
    latest_version = latest_version_row[0]

    terminal = _terminal_states()

    rows = (
        db.query(TradeSetup, Stock.symbol)
        .join(Stock, TradeSetup.stock_id == Stock.id)
        .filter(TradeSetup.strategy_version == latest_version)
        .filter(~TradeSetup.state.in_(terminal))
        .order_by(TradeSetup.score.desc())
        .all()
    )

    results: list[ActiveCall] = []
    for setup, symbol in rows:
        results.append(
            ActiveCall(
                symbol=symbol,
                setup_type=setup.setup_type,
                state=setup.state,
                entry_price=setup.entry_price,
                stop_loss=setup.stop_loss,
                targets=targets_to_list(
                    setup.target_1r,
                    setup.target_1_5r,
                    setup.target_2r,
                    setup.target_3r,
                    setup.nearest_structural_target,
                ),
                confidence=setup.score,
                reason=parse_json_field(setup.score_breakdown),
                ltp=_fetch_ltp_safe(symbol),
            )
        )

    return results
