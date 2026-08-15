"""POST /api/backtest-runs — trigger a new backtest/simulator.py run as a
background task. See docs/backend.md's illustrative endpoint table and
docs/api.md's scope note (backtests are not part of the daily EOD chain).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.api.deps import generate_strategy_version
from app.schemas.backtests import BacktestRunTriggerResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest-runs", tags=["backtest-runs"])

DEFAULT_LOOKBACK_DAYS = 365


class BacktestRunRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


def _run_backtest_job(strategy_version: str, start_date: date, end_date: date) -> None:
    try:
        from app.backtest.simulator import run_backtest_and_persist
    except ImportError as exc:
        logger.error("app.backtest.simulator not available, backtest run %s aborted: %s", strategy_version, exc)
        return

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        run_backtest_and_persist(db, strategy_version=strategy_version, start_date=start_date, end_date=end_date)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Backtest run %s failed", strategy_version)
    finally:
        db.close()


@router.post("", response_model=BacktestRunTriggerResponse, status_code=202)
def trigger_backtest_run(
    request: BacktestRunRequest,
    background_tasks: BackgroundTasks,
) -> BacktestRunTriggerResponse:
    end_date = request.end_date or date.today()
    start_date = request.start_date or (end_date - timedelta(days=DEFAULT_LOOKBACK_DAYS))

    if start_date >= end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    strategy_version = generate_strategy_version()

    background_tasks.add_task(_run_backtest_job, strategy_version, start_date, end_date)

    return BacktestRunTriggerResponse(
        strategy_version=strategy_version,
        start_date=start_date,
        end_date=end_date,
        status="queued",
    )
