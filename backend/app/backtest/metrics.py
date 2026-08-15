"""Win rate, profit factor, breakdowns by score bucket/sector/day-of-week/regime/setup_type.

See docs/engine.md#modules and docs/db.md (backtest_results.breakdown_by_*
columns must be breakable down by setup_type too, per db.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.breakout.scoring import score_tier


@dataclass(frozen=True)
class BacktestMetrics:
    total_trades: int
    win_rate: float | None
    profit_factor: float | None
    avg_mfe: float | None
    avg_mae: float | None
    avg_holding_days: float | None
    breakdown_by_score_bucket: dict = field(default_factory=dict)
    breakdown_by_sector: dict = field(default_factory=dict)
    breakdown_by_day_of_week: dict = field(default_factory=dict)
    breakdown_by_regime: dict = field(default_factory=dict)


def _win_rate(trades: pd.DataFrame) -> float | None:
    if trades.empty:
        return None
    closed = trades[trades["exit_reason"].isin(["TARGET_HIT", "INVALIDATED"])]
    if closed.empty:
        return None
    wins = (closed["exit_reason"] == "TARGET_HIT").sum()
    return float(wins / len(closed))


def _profit_factor(trades: pd.DataFrame) -> float | None:
    if trades.empty or "pnl" not in trades.columns:
        return None
    closed = trades[trades["exit_reason"].isin(["TARGET_HIT", "INVALIDATED"])]
    if closed.empty:
        return None
    gross_profit = closed.loc[closed["pnl"] > 0, "pnl"].sum()
    gross_loss = -closed.loc[closed["pnl"] < 0, "pnl"].sum()
    if gross_loss == 0:
        return None  # undefined — avoid divide-by-zero rather than report infinity
    return float(gross_profit / gross_loss)


def _breakdown_by(trades: pd.DataFrame, key: str) -> dict:
    if trades.empty or key not in trades.columns:
        return {}
    closed = trades[trades["exit_reason"].isin(["TARGET_HIT", "INVALIDATED"])]
    if closed.empty:
        return {}
    result = {}
    for group_val, group_df in closed.groupby(key):
        wins = (group_df["exit_reason"] == "TARGET_HIT").sum()
        result[str(group_val)] = {"trades": int(len(group_df)), "win_rate": float(wins / len(group_df))}
    return result


def compute_backtest_metrics(trades: pd.DataFrame) -> BacktestMetrics:
    """trades: one row per trade_setup with columns including at minimum
    [score, sector, entry_date, exit_reason, mfe, mae, holding_days,
    regime, pnl]. Produced by simulator.py's replay loop.
    """
    if trades.empty:
        return BacktestMetrics(total_trades=0, win_rate=None, profit_factor=None, avg_mfe=None, avg_mae=None, avg_holding_days=None)

    df = trades.copy()
    if "score" in df.columns:
        df["score_bucket"] = df["score"].apply(score_tier)
    if "entry_date" in df.columns:
        df["day_of_week"] = pd.to_datetime(df["entry_date"]).dt.day_name()

    closed = df[df["exit_reason"].isin(["TARGET_HIT", "INVALIDATED"])]

    return BacktestMetrics(
        total_trades=len(df),
        win_rate=_win_rate(df),
        profit_factor=_profit_factor(df),
        avg_mfe=float(closed["mfe"].mean()) if not closed.empty and "mfe" in closed.columns else None,
        avg_mae=float(closed["mae"].mean()) if not closed.empty and "mae" in closed.columns else None,
        avg_holding_days=float(closed["holding_days"].mean()) if not closed.empty and "holding_days" in closed.columns else None,
        breakdown_by_score_bucket=_breakdown_by(df, "score_bucket"),
        breakdown_by_sector=_breakdown_by(df, "sector"),
        breakdown_by_day_of_week=_breakdown_by(df, "day_of_week"),
        breakdown_by_regime=_breakdown_by(df, "regime"),
    )
