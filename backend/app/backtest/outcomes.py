"""Tracks MFE/MAE/target-hit/SL-hit per trade — docs/engine.md#modules.

No maximum holding period / time-stop in v1 — a trade stays TRADE_ACTIVE
until TARGET_HIT, INVALIDATED (stop-loss), or SESSION_END.

MFE/MAE are fractional returns relative to entry_price (e.g. 0.05 = +5%),
NOT raw rupee price differences. Found live 2026-08-17: raw-rupee MFE/MAE
(and simulator.py's raw-rupee pnl) let whichever trades happened to be in
high-priced stocks (a ₹30,000 stock's 10% move is ~₹3,000 raw, a ₹120
stock's same 10% move is ~₹12) dominate every aggregate metric in
metrics.py regardless of real trade quality — profit_factor readings that
night were comparing apples to oranges across price levels. Normalizing
here fixes it at the source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class TradeOutcomeResult:
    mfe: float
    mae: float
    target_hit: str | None  # "1R" | "1.5R" | "2R" | "3R" | "STRUCTURAL" | None
    sl_hit: bool
    exit_price: float | None
    exit_date: date | None
    exit_reason: str  # TARGET_HIT | INVALIDATED | SESSION_END
    holding_days: int | None


def track_trade_outcome(
    entry_date: date,
    entry_price: float,
    stop_loss: float,
    targets: dict,
    bars_after_entry: pd.DataFrame,  # OHLC rows strictly after entry_date, ascending
) -> TradeOutcomeResult:
    """Walks forward bar-by-bar from entry, tracking favorable/adverse excursion
    and checking SL/target hits. Stop-loss is checked before targets on any
    bar where both could plausibly trigger (conservative assumption — intraday
    ordering within a daily bar is unknown).
    """
    if bars_after_entry.empty:
        return TradeOutcomeResult(
            mfe=0.0, mae=0.0, target_hit=None, sl_hit=False, exit_price=None, exit_date=None, exit_reason="SESSION_END", holding_days=0
        )

    mfe = 0.0
    mae = 0.0
    target_order = [("3R", targets.get("target_3r")), ("2R", targets.get("target_2r")), ("1.5R", targets.get("target_1_5r")), ("1R", targets.get("target_1r"))]
    structural = targets.get("nearest_structural_target")

    for i, (idx, bar) in enumerate(bars_after_entry.iterrows(), start=1):
        high = float(bar["high"])
        low = float(bar["low"])

        mfe = max(mfe, (high - entry_price) / entry_price)
        mae = min(mae, (low - entry_price) / entry_price)

        if low <= stop_loss:
            exit_date = idx.date() if hasattr(idx, "date") else idx
            return TradeOutcomeResult(
                mfe=mfe, mae=mae, target_hit=None, sl_hit=True, exit_price=stop_loss, exit_date=exit_date, exit_reason="INVALIDATED", holding_days=i
            )

        if structural is not None and high >= structural:
            exit_date = idx.date() if hasattr(idx, "date") else idx
            return TradeOutcomeResult(
                mfe=mfe, mae=mae, target_hit="STRUCTURAL", sl_hit=False, exit_price=structural, exit_date=exit_date, exit_reason="TARGET_HIT", holding_days=i
            )

        for label, level in target_order:
            if level is not None and high >= level:
                exit_date = idx.date() if hasattr(idx, "date") else idx
                return TradeOutcomeResult(
                    mfe=mfe, mae=mae, target_hit=label, sl_hit=False, exit_price=level, exit_date=exit_date, exit_reason="TARGET_HIT", holding_days=i
                )

    # Backtest window closed with position still open — logged as-is, not force-closed.
    return TradeOutcomeResult(
        mfe=mfe, mae=mae, target_hit=None, sl_hit=False, exit_price=None, exit_date=None, exit_reason="SESSION_END", holding_days=len(bars_after_entry)
    )
