"""Pydantic response models for /api/backtests* — see docs/api.md."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class BacktestRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy_version: str
    run_date: date
    total_trades: int
    win_rate: float | None
    profit_factor: float | None


class BacktestMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    strategy_version: str
    total_trades: int
    win_rate: float | None
    profit_factor: float | None
    avg_mfe: float | None
    avg_mae: float | None
    avg_holding_days: float | None
    breakdown_by_score_bucket: dict | list | None
    breakdown_by_sector: dict | list | None
    breakdown_by_day_of_week: dict | list | None
    breakdown_by_regime: dict | list | None


class TradeOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mfe: float | None
    mae: float | None
    target_hit: str | None
    sl_hit: bool
    holding_days: int | None
    exit_reason: str | None


class TradeSetupOut(BaseModel):
    trade_setup_id: int
    symbol: str
    setup_type: str
    entry_date: date | None
    entry_price: float | None
    stop_loss: float
    # Flat array [target_1r, target_1_5r, target_2r, target_3r,
    # nearest_structural_target] — docs/api.md's `targets` field. Reassembled
    # from trade_setups' five Numeric columns via app.api.deps.targets_to_list
    # so the wire contract stays stable even though DB storage is
    # column-per-target. Matches frontend/src/components/backtest/TradeList.jsx's
    # Array.isArray(targets)/targets.map(...) consumption.
    targets: list[float | None]
    score: float
    outcome: TradeOutcomeOut | None


class BacktestRunTriggerResponse(BaseModel):
    strategy_version: str
    start_date: date
    end_date: date
    status: str
