"""Breakout state machine — see docs/engine.md#breakout-state-machine.

WATCH -> APPROACHING -> BREAKOUT_ATTEMPT -> CANDLE_CONFIRMATION ->
VOLUME_CONFIRMATION -> CONFIRMED -> RETEST_PENDING -> RETEST_CONFIRMED ->
TRADE_ACTIVE -> TARGET_HIT / INVALIDATED / SESSION_END

Stateless per-call function: (current_state, bar_history_up_to_today,
resistance_cluster) -> (new_state, transition_event). Caller (simulator)
owns state storage. This function must never receive bars beyond "today" —
the no-look-ahead guarantee is enforced by the caller slicing bar_history,
and is unit-tested by asserting output is unchanged when future bars are
appended to the input (see backend/tests/test_breakout_engine.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.breakout.atr_buffer import atr_entry_buffer
from app.breakout.rvol import relative_volume
from app.breakout.states import BreakoutState
from app.breakout.target_ladder import target_ladder
from app.market.candles import candle_quality, upper_wick_pct
from app.market.levels import Level

# Thresholds — kept as module constants for now; promote to config.py if the
# walk-forward optimization follow-up needs to tune them per docs/engine.md.
APPROACH_PROXIMITY_PCT = 2.0  # within 2% of resistance triggers APPROACHING
MIN_RVOL_CONFIRM = 1.5
MIN_BODY_PCT = 40.0
MAX_UPPER_WICK_REJECT_PCT = 60.0
RETEST_TOLERANCE_PCT = 1.5
RETEST_MAX_BARS = 10


@dataclass(frozen=True)
class EngineResult:
    new_state: str
    transition_event: str | None
    entry_price: float | None = None
    stop_loss: float | None = None
    targets: dict | None = None


def _resistance_target_above(close: float, clusters: list[Level]) -> Level | None:
    """Nearest resistance strictly above today's close — "what am I watching
    to break out above." Used only by WATCH, where a level already below
    close would be a level already broken, not something to watch for.
    """
    above = [c for c in clusters if c.price >= close]
    if not above:
        return None
    return min(above, key=lambda c: c.price)


def _resistance_being_tested(close: float, clusters: list[Level]) -> Level | None:
    """The resistance level closest to today's close, on either side.

    Used by every state from APPROACHING onward. Deliberately NOT filtered
    to "clusters at/above close" like _resistance_target_above — once price
    closes above a resistance level (the entire point of BREAKOUT_ATTEMPT
    onward), that level would otherwise vanish from consideration exactly
    when the state machine most needs to keep referencing it (candle
    confirmation, RVOL confirmation, retest). Absolute distance to close is
    the correct "which level is this bar about" query once a target has
    already been identified by WATCH/APPROACHING.
    """
    if not clusters:
        return None
    return min(clusters, key=lambda c: abs(c.price - close))


def advance_breakout_state(
    current_state: str,
    bar_history: pd.DataFrame,
    resistance_clusters: list[Level],
    support_levels: list[Level] | None = None,
    atr: float | None = None,
    bars_since_confirmed: int = 0,
) -> EngineResult:
    """bar_history: OHLCV truncated to "today" (inclusive), ascending by date.
    resistance_clusters: computed from the same truncated history.
    support_levels: swing lows below current price, for stop-loss placement.
    """
    if bar_history.empty:
        return EngineResult(new_state=current_state, transition_event=None)

    today = bar_history.iloc[-1]
    close = float(today["close"])

    if current_state == BreakoutState.WATCH:
        target = _resistance_target_above(close, resistance_clusters)
        if target is None:
            return EngineResult(new_state=BreakoutState.WATCH, transition_event=None)
        proximity = (target.price - close) / target.price * 100
        if 0 <= proximity <= APPROACH_PROXIMITY_PCT:
            return EngineResult(new_state=BreakoutState.APPROACHING, transition_event="PRICE_NEAR_RESISTANCE")
        return EngineResult(new_state=BreakoutState.WATCH, transition_event=None)

    if current_state == BreakoutState.APPROACHING:
        # _resistance_being_tested, not _resistance_target_above: a close
        # that has already moved above the level must still resolve to that
        # same level (to fire CLOSE_ABOVE_RESISTANCE below), not lose it.
        target = _resistance_being_tested(close, resistance_clusters)
        if target is None:
            return EngineResult(new_state=BreakoutState.WATCH, transition_event="TARGET_LOST")
        if close > target.price:
            return EngineResult(new_state=BreakoutState.BREAKOUT_ATTEMPT, transition_event="CLOSE_ABOVE_RESISTANCE")
        proximity = (target.price - close) / target.price * 100
        if proximity > APPROACH_PROXIMITY_PCT:
            return EngineResult(new_state=BreakoutState.WATCH, transition_event="PULLED_AWAY")
        return EngineResult(new_state=BreakoutState.APPROACHING, transition_event=None)

    if current_state == BreakoutState.BREAKOUT_ATTEMPT:
        target = _resistance_being_tested(close, resistance_clusters)
        resistance_price = target.price if target else float(today["low"])
        if close <= resistance_price:
            return EngineResult(new_state=BreakoutState.NOT_CONFIRMED, transition_event="CLOSE_BACK_BELOW_RESISTANCE")
        cq = candle_quality(float(today["open"]), float(today["high"]), float(today["low"]), close)
        wick = upper_wick_pct(float(today["open"]), float(today["high"]), float(today["low"]), close)
        if wick > MAX_UPPER_WICK_REJECT_PCT:
            return EngineResult(new_state=BreakoutState.NOT_CONFIRMED, transition_event="SEVERE_REJECTION_WICK")
        if cq.body_pct < MIN_BODY_PCT:
            return EngineResult(new_state=BreakoutState.BREAKOUT_ATTEMPT, transition_event="WEAK_CANDLE_BODY")
        return EngineResult(new_state=BreakoutState.CANDLE_CONFIRMATION, transition_event="CANDLE_QUALITY_OK")

    if current_state == BreakoutState.CANDLE_CONFIRMATION:
        rvol = relative_volume(bar_history["volume"])
        if rvol is None:
            return EngineResult(new_state=BreakoutState.CANDLE_CONFIRMATION, transition_event=None)
        if rvol < MIN_RVOL_CONFIRM:
            return EngineResult(new_state=BreakoutState.NOT_CONFIRMED, transition_event="RVOL_BELOW_MINIMUM")
        return EngineResult(new_state=BreakoutState.VOLUME_CONFIRMATION, transition_event="RVOL_CONFIRMED")

    if current_state == BreakoutState.VOLUME_CONFIRMATION:
        target = _resistance_being_tested(close, resistance_clusters)
        breakout_level = target.price if target else close
        entry = atr_entry_buffer(breakout_level, atr)
        stop = _pick_stop_loss(close, support_levels)
        targets = target_ladder(entry, stop, resistance_clusters)
        return EngineResult(
            new_state=BreakoutState.CONFIRMED,
            transition_event="CONFIRMED",
            entry_price=entry,
            stop_loss=stop,
            targets=targets,
        )

    if current_state == BreakoutState.CONFIRMED:
        return EngineResult(new_state=BreakoutState.RETEST_PENDING, transition_event="AWAITING_RETEST")

    if current_state == BreakoutState.RETEST_PENDING:
        target = _resistance_being_tested(close, resistance_clusters)
        breakout_level = target.price if target else close
        if close < breakout_level * (1 - RETEST_TOLERANCE_PCT / 100):
            return EngineResult(new_state=BreakoutState.INVALIDATED, transition_event="CLOSE_BELOW_BREAKOUT_LEVEL")
        if bars_since_confirmed >= RETEST_MAX_BARS:
            return EngineResult(new_state=BreakoutState.RETEST_CONFIRMED, transition_event="RETEST_WINDOW_ELAPSED")
        near_level = abs(close - breakout_level) / breakout_level * 100 <= RETEST_TOLERANCE_PCT
        if near_level and close >= breakout_level:
            return EngineResult(new_state=BreakoutState.RETEST_CONFIRMED, transition_event="RETEST_HELD")
        return EngineResult(new_state=BreakoutState.RETEST_PENDING, transition_event=None)

    if current_state == BreakoutState.RETEST_CONFIRMED:
        return EngineResult(new_state=BreakoutState.TRADE_ACTIVE, transition_event="ENTRY_TRIGGERED")

    # TRADE_ACTIVE and terminal states are managed by backtest/execution.py,
    # which tracks entry/target/SL against subsequent bars — this function
    # only owns pre-entry state transitions.
    return EngineResult(new_state=current_state, transition_event=None)


def _pick_stop_loss(close: float, support_levels: list[Level] | None) -> float:
    """Nearest structural support below entry — swing low / resistance-turned-
    support / consolidation low, per docs/engine.md. Falls back to a flat 3%
    stop if no structural level is available yet (thin early history).
    """
    if support_levels:
        below = [lvl for lvl in support_levels if lvl.price < close]
        if below:
            return max(below, key=lambda lvl: lvl.price).price
    return close * 0.97


