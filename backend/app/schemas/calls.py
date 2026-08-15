"""Pydantic response models for /api/calls — see docs/api.md."""
from __future__ import annotations

from pydantic import BaseModel


class ActiveCall(BaseModel):
    symbol: str
    setup_type: str
    state: str
    entry_price: float | None
    stop_loss: float
    # Flat array [target_1r, target_1_5r, target_2r, target_3r,
    # nearest_structural_target] — docs/api.md's `targets` field, reassembled
    # via app.api.deps.targets_to_list. See schemas/backtests.py's
    # TradeSetupOut.targets for the same contract on the backtest-trades endpoint.
    targets: list[float | None]
    confidence: float
    reason: dict | list | str | None
    ltp: float | None
