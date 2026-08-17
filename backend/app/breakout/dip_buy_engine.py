"""Dip-buy state machine — see docs/engine.md#dip-buy-setup.

UPTREND_CONFIRMED -> PULLBACK_IN_PROGRESS -> SUPPORT_TEST -> REVERSAL_CANDLE
-> VOLUME_CONFIRMATION -> CONFIRMED -> RETEST_PENDING -> RETEST_CONFIRMED ->
TRADE_ACTIVE -> TARGET_HIT / INVALIDATED / SESSION_END

Same stateless-function shape and no-look-ahead guarantee as breakout_engine:
(current_state, bar_history_up_to_today, support_cluster) -> (new_state,
transition_event).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.breakout.atr_buffer import atr_entry_buffer
from app.breakout.breakout_engine import MAX_STOP_LOSS_PCT
from app.breakout.rvol import relative_volume
from app.breakout.states import DipBuyState
from app.market.candles import detect_reversal_pattern
from app.breakout.target_ladder import target_ladder
from app.market.levels import Level

# Loosened 2026-08-17, then reverted same day — see breakout_engine.py's
# matching comment for why (real backtest showed the loosened settings
# produced a losing strategy, profit_factor 0.46).
RETEST_TOLERANCE_PCT = 1.5
RETEST_MAX_BARS = 10
MIN_RVOL_CONFIRM = 1.3  # lower bar than breakout's 1.5 — reversal off a pullback, not a breakout surge
SUPPORT_TOLERANCE_PCT = 1.5


@dataclass(frozen=True)
class EngineResult:
    new_state: str
    transition_event: str | None
    entry_price: float | None = None
    stop_loss: float | None = None
    targets: dict | None = None


def _uptrend_precondition(bar_history: pd.DataFrame, ema20: float | None, ema50: float | None) -> bool:
    """Price above EMA50, EMA20 above EMA50, no lower-low swing structure —
    docs/engine.md's dip-buy precondition.
    """
    if ema20 is None or ema50 is None:
        return False
    close = float(bar_history["close"].iloc[-1])
    if close <= ema50 or ema20 <= ema50:
        return False

    from app.market.levels import swing_highs_lows

    swings = [s for s in swing_highs_lows(bar_history) if s.level_type == "SWING_LOW"]
    if len(swings) >= 2 and swings[-1].price < swings[-2].price:
        return False  # lower low = downtrend continuation, not a dip
    return True


def _nearest_support(close: float, clusters: list[Level]) -> Level | None:
    """The support level closest to today's close, on either side.

    Deliberately NOT filtered to "clusters at/below close" — mirrors
    breakout_engine._nearest_resistance's documented reasoning: once price
    closes below a support level (exactly what SUPPORT_TEST's
    SUPPORT_CLOSED_BELOW rejection needs to detect), a "clusters <= close"
    filter would find nothing and the level would vanish from consideration
    right when the state machine most needs to keep referencing it.
    """
    if not clusters:
        return None
    return min(clusters, key=lambda c: abs(c.price - close))


def advance_dip_buy_state(
    current_state: str,
    bar_history: pd.DataFrame,
    support_clusters: list[Level],
    resistance_clusters: list[Level] | None = None,
    ema20: float | None = None,
    ema50: float | None = None,
    atr: float | None = None,
    pullback_low: float | None = None,
    bars_since_confirmed: int = 0,
) -> EngineResult:
    if bar_history.empty:
        return EngineResult(new_state=current_state, transition_event=None)

    today = bar_history.iloc[-1]
    close = float(today["close"])

    if current_state == DipBuyState.UPTREND_CONFIRMED:
        if not _uptrend_precondition(bar_history, ema20, ema50):
            return EngineResult(new_state=DipBuyState.NOT_CONFIRMED, transition_event="UPTREND_PRECONDITION_FAILED")
        prior_close = float(bar_history["close"].iloc[-2]) if len(bar_history) >= 2 else close
        if close < prior_close:
            return EngineResult(new_state=DipBuyState.PULLBACK_IN_PROGRESS, transition_event="PULLBACK_STARTED")
        return EngineResult(new_state=DipBuyState.UPTREND_CONFIRMED, transition_event=None)

    if current_state == DipBuyState.PULLBACK_IN_PROGRESS:
        if not _uptrend_precondition(bar_history, ema20, ema50):
            return EngineResult(new_state=DipBuyState.INVALIDATED, transition_event="TREND_BROKEN_DURING_PULLBACK")
        support = _nearest_support(close, support_clusters)
        if support is None:
            return EngineResult(new_state=DipBuyState.PULLBACK_IN_PROGRESS, transition_event=None)
        proximity = abs(close - support.price) / support.price * 100
        if proximity <= SUPPORT_TOLERANCE_PCT:
            return EngineResult(new_state=DipBuyState.SUPPORT_TEST, transition_event="PRICE_AT_SUPPORT")
        return EngineResult(new_state=DipBuyState.PULLBACK_IN_PROGRESS, transition_event=None)

    if current_state == DipBuyState.SUPPORT_TEST:
        support = _nearest_support(close, support_clusters)
        support_price = support.price if support else float(today["low"])
        if close < support_price * (1 - SUPPORT_TOLERANCE_PCT / 100):
            return EngineResult(new_state=DipBuyState.NOT_CONFIRMED, transition_event="SUPPORT_CLOSED_BELOW")
        pattern = detect_reversal_pattern(bar_history.tail(3))["pattern"]
        if pattern is None:
            return EngineResult(new_state=DipBuyState.SUPPORT_TEST, transition_event=None)
        if close < support_price:
            return EngineResult(new_state=DipBuyState.SUPPORT_TEST, transition_event=None)  # need close back above support
        return EngineResult(new_state=DipBuyState.REVERSAL_CANDLE, transition_event=f"REVERSAL_PATTERN_{pattern}")

    if current_state == DipBuyState.REVERSAL_CANDLE:
        rvol = relative_volume(bar_history["volume"])
        if rvol is None:
            return EngineResult(new_state=DipBuyState.REVERSAL_CANDLE, transition_event=None)
        if rvol < MIN_RVOL_CONFIRM:
            return EngineResult(new_state=DipBuyState.NOT_CONFIRMED, transition_event="REVERSAL_RVOL_BELOW_MINIMUM")
        return EngineResult(new_state=DipBuyState.VOLUME_CONFIRMATION, transition_event="RVOL_CONFIRMED")

    if current_state == DipBuyState.VOLUME_CONFIRMATION:
        entry = atr_entry_buffer(close, atr, buffer_atr_multiple=0.05)
        dip_low = pullback_low if pullback_low is not None else float(bar_history["low"].tail(10).min())
        # Same MAX_STOP_LOSS_PCT cap as breakout_engine._pick_stop_loss, same
        # reason — a deep-enough pullback low shouldn't produce an unbounded
        # risk stop. dip_low is usually already tight (recent price action,
        # not an old structural level), so this cap rarely binds here, but
        # applied for consistency/safety rather than assuming it never will.
        stop = max(dip_low, entry * (1 - MAX_STOP_LOSS_PCT / 100))  # stop below the dip's own low, not a wider structural stop — docs/engine.md
        targets = target_ladder(entry, stop, resistance_clusters or [])
        return EngineResult(
            new_state=DipBuyState.CONFIRMED,
            transition_event="CONFIRMED",
            entry_price=entry,
            stop_loss=stop,
            targets=targets,
        )

    if current_state == DipBuyState.CONFIRMED:
        return EngineResult(new_state=DipBuyState.RETEST_PENDING, transition_event="AWAITING_RETEST")

    if current_state == DipBuyState.RETEST_PENDING:
        if pullback_low is not None and float(today["low"]) < pullback_low:
            return EngineResult(new_state=DipBuyState.INVALIDATED, transition_event="NEW_LOW_BELOW_DIP")
        if bars_since_confirmed >= RETEST_MAX_BARS:
            return EngineResult(new_state=DipBuyState.RETEST_CONFIRMED, transition_event="RETEST_WINDOW_ELAPSED")
        return EngineResult(new_state=DipBuyState.RETEST_PENDING, transition_event=None)

    if current_state == DipBuyState.RETEST_CONFIRMED:
        return EngineResult(new_state=DipBuyState.TRADE_ACTIVE, transition_event="ENTRY_TRIGGERED")

    return EngineResult(new_state=current_state, transition_event=None)


def _target_ladder(entry: float, stop: float, resistance_clusters: list[Level]) -> dict:
    risk = entry - stop
    ladder = {
        "target_1r": entry + 1.0 * risk,
        "target_1_5r": entry + 1.5 * risk,
        "target_2r": entry + 2.0 * risk,
        "target_3r": entry + 3.0 * risk,
    }
    above = [c for c in resistance_clusters if c.price > entry]
    ladder["nearest_structural_target"] = min(above, key=lambda c: c.price).price if above else None
    return ladder
