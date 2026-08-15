"""GET /api/backtests, /api/backtests/{strategy_version}/metrics, /trades — see docs/api.md."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import parse_json_field, targets_to_list
from app.db.models import BacktestResult, Stock, TradeOutcome, TradeSetup
from app.db.session import get_db
from app.schemas.backtests import (
    BacktestMetrics,
    BacktestRunSummary,
    TradeOutcomeOut,
    TradeSetupOut,
)

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

DEFAULT_LIMIT = 50


@router.get("", response_model=list[BacktestRunSummary])
def list_backtests(db: Session = Depends(get_db)) -> list[BacktestRunSummary]:
    latest_per_version = (
        db.query(
            BacktestResult.strategy_version,
            func.max(BacktestResult.run_date).label("run_date"),
        )
        .group_by(BacktestResult.strategy_version)
        .subquery()
    )

    rows = (
        db.query(BacktestResult)
        .join(
            latest_per_version,
            (BacktestResult.strategy_version == latest_per_version.c.strategy_version)
            & (BacktestResult.run_date == latest_per_version.c.run_date),
        )
        .order_by(BacktestResult.run_date.desc())
        .all()
    )

    return [
        BacktestRunSummary(
            strategy_version=r.strategy_version,
            run_date=r.run_date,
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            profit_factor=r.profit_factor,
        )
        for r in rows
    ]


@router.get("/{strategy_version}/metrics", response_model=BacktestMetrics)
def get_backtest_metrics(strategy_version: str, db: Session = Depends(get_db)) -> BacktestMetrics:
    row = (
        db.query(BacktestResult)
        .filter(BacktestResult.strategy_version == strategy_version)
        .order_by(BacktestResult.run_date.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No backtest results for strategy_version: {strategy_version}")

    return BacktestMetrics(
        strategy_version=row.strategy_version,
        total_trades=row.total_trades,
        win_rate=row.win_rate,
        profit_factor=row.profit_factor,
        avg_mfe=row.avg_mfe,
        avg_mae=row.avg_mae,
        avg_holding_days=row.avg_holding_days,
        breakdown_by_score_bucket=parse_json_field(row.breakdown_by_score_bucket),
        breakdown_by_sector=parse_json_field(row.breakdown_by_sector),
        breakdown_by_day_of_week=parse_json_field(row.breakdown_by_day_of_week),
        breakdown_by_regime=parse_json_field(row.breakdown_by_regime),
    )


@router.get("/{strategy_version}/trades", response_model=list[TradeSetupOut])
def get_backtest_trades(
    strategy_version: str,
    symbol: str | None = Query(default=None),
    score_min: float | None = Query(default=None),
    setup_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[TradeSetupOut]:
    exists = (
        db.query(BacktestResult.id)
        .filter(BacktestResult.strategy_version == strategy_version)
        .first()
    )
    if exists is None:
        raise HTTPException(status_code=404, detail=f"No backtest results for strategy_version: {strategy_version}")

    query = (
        db.query(TradeSetup, Stock.symbol, TradeOutcome)
        .join(Stock, TradeSetup.stock_id == Stock.id)
        .outerjoin(TradeOutcome, TradeOutcome.trade_setup_id == TradeSetup.id)
        .filter(TradeSetup.strategy_version == strategy_version)
    )

    if symbol:
        query = query.filter(Stock.symbol == symbol.upper())
    if score_min is not None:
        query = query.filter(TradeSetup.score >= score_min)
    if setup_type:
        query = query.filter(TradeSetup.setup_type == setup_type.upper())

    rows = query.order_by(TradeSetup.signal_date.desc()).offset(offset).limit(limit).all()

    results: list[TradeSetupOut] = []
    for setup, symbol_val, outcome in rows:
        results.append(
            TradeSetupOut(
                trade_setup_id=setup.id,
                symbol=symbol_val,
                setup_type=setup.setup_type,
                entry_date=setup.entry_date,
                entry_price=setup.entry_price,
                stop_loss=setup.stop_loss,
                targets=targets_to_list(
                    setup.target_1r,
                    setup.target_1_5r,
                    setup.target_2r,
                    setup.target_3r,
                    setup.nearest_structural_target,
                ),
                score=setup.score,
                outcome=(
                    TradeOutcomeOut(
                        mfe=outcome.mfe,
                        mae=outcome.mae,
                        target_hit=outcome.target_hit,
                        sl_hit=outcome.sl_hit,
                        holding_days=outcome.holding_days,
                        exit_reason=outcome.exit_reason,
                    )
                    if outcome is not None
                    else None
                ),
            )
        )

    return results
