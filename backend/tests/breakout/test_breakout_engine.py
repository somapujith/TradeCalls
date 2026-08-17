"""Tests for app.breakout.breakout_engine.advance_breakout_state.

Covers every state transition in the WATCH -> ... -> TRADE_ACTIVE chain,
both the "advance" and "stay/reject" branches, the hard-rejection paths
(severe upper wick, RVOL below minimum, close back below resistance), and
the no-look-ahead invariant (identical output when future bars are
appended to the input and re-sliced back to "today").
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.breakout.breakout_engine import (
    APPROACH_PROXIMITY_PCT,
    MAX_UPPER_WICK_REJECT_PCT,
    MIN_BODY_PCT,
    MIN_RVOL_CONFIRM,
    RETEST_MAX_BARS,
    RETEST_TOLERANCE_PCT,
    advance_breakout_state,
)
from app.breakout.states import BreakoutState
from app.market.levels import Level

RESISTANCE = Level(level_type="RESISTANCE_CLUSTER", price=100.0, strength=2.0)
SUPPORT = Level(level_type="SWING_LOW", price=95.0, strength=1.0)


def _bar_row(open_, high, low, close, volume=100_000):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


# --- empty history -----------------------------------------------------------


def test_empty_bar_history_returns_current_state_unchanged():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = advance_breakout_state(BreakoutState.WATCH, empty, [RESISTANCE])

    assert result.new_state == BreakoutState.WATCH
    assert result.transition_event is None


# --- WATCH ---------------------------------------------------------------


def test_watch_no_resistance_clusters_stays_watch(flat_bars_factory):
    bars = flat_bars_factory(5, price=100.0)

    result = advance_breakout_state(BreakoutState.WATCH, bars, [])

    assert result.new_state == BreakoutState.WATCH
    assert result.transition_event is None


def test_watch_far_from_resistance_stays_watch(flat_bars_factory):
    bars = flat_bars_factory(5, price=90.0)  # >2% away from 100

    result = advance_breakout_state(BreakoutState.WATCH, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.WATCH
    assert result.transition_event is None


def test_watch_within_proximity_advances_to_approaching(flat_bars_factory):
    bars = flat_bars_factory(5, price=98.5)  # 1.5% below 100, within 2%

    result = advance_breakout_state(BreakoutState.WATCH, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.APPROACHING
    assert result.transition_event == "PRICE_NEAR_RESISTANCE"


def test_watch_proximity_boundary_at_exactly_approach_pct_advances(flat_bars_factory):
    # proximity == APPROACH_PROXIMITY_PCT exactly -> boundary is inclusive (<=)
    boundary_price = 100.0 * (1 - APPROACH_PROXIMITY_PCT / 100)
    bars = flat_bars_factory(5, price=boundary_price)

    result = advance_breakout_state(BreakoutState.WATCH, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.APPROACHING


def test_watch_close_exactly_at_resistance_advances(flat_bars_factory):
    # proximity == 0 (close == resistance) is within [0, APPROACH_PROXIMITY_PCT]
    bars = flat_bars_factory(5, price=100.0)

    result = advance_breakout_state(BreakoutState.WATCH, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.APPROACHING


def test_watch_no_look_ahead(bars_factory):
    rows = [_bar_row(98, 99, 97, 98, 100_000) for _ in range(5)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(BreakoutState.WATCH, bars_today, [RESISTANCE])

    future_rows = [_bar_row(999, 999, 999, 999, 100_000) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(BreakoutState.WATCH, truncated, [RESISTANCE])

    assert result_recomputed == result_today


# --- APPROACHING -----------------------------------------------------------


def test_approaching_target_lost_when_no_cluster_above(flat_bars_factory):
    bars = flat_bars_factory(5, price=98.0)

    result = advance_breakout_state(BreakoutState.APPROACHING, bars, [])

    assert result.new_state == BreakoutState.WATCH
    assert result.transition_event == "TARGET_LOST"


def test_approaching_pulled_away_reverts_to_watch(flat_bars_factory):
    bars = flat_bars_factory(5, price=90.0)  # far below resistance, > tolerance

    result = advance_breakout_state(BreakoutState.APPROACHING, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.WATCH
    assert result.transition_event == "PULLED_AWAY"


def test_approaching_stays_when_still_within_proximity(flat_bars_factory):
    bars = flat_bars_factory(5, price=99.0)  # within 2%, still below resistance

    result = advance_breakout_state(BreakoutState.APPROACHING, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.APPROACHING
    assert result.transition_event is None


def test_approaching_close_above_resistance_reaches_breakout_attempt(bars_factory):
    """Regression test for a fixed source bug: the target lookup used by
    APPROACHING previously filtered clusters to `price >= close`, so once
    close had already closed above a resistance level, that level could
    never again be "the target" — the `target is None` branch fired
    (TARGET_LOST / WATCH) instead of CLOSE_ABOVE_RESISTANCE / BREAKOUT_ATTEMPT,
    even though price had genuinely broken out. Fixed by having APPROACHING
    (and every state after it) resolve the target by nearest absolute
    distance to close instead of "still above close" — see
    `_resistance_being_tested` in breakout_engine.py.
    """
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.5, 103, 100.2, 101.0))
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.APPROACHING, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.BREAKOUT_ATTEMPT
    assert result.transition_event == "CLOSE_ABOVE_RESISTANCE"


def test_approaching_no_look_ahead(bars_factory):
    rows = [_bar_row(98, 99, 97, 98) for _ in range(5)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(BreakoutState.APPROACHING, bars_today, [RESISTANCE])

    future_rows = [_bar_row(50, 50, 50, 50) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(BreakoutState.APPROACHING, truncated, [RESISTANCE])

    assert result_recomputed == result_today


# --- BREAKOUT_ATTEMPT --------------------------------------------------------


def test_breakout_attempt_close_back_below_resistance_not_confirmed(bars_factory):
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.5, 101, 99.0, 99.5))  # closes back below 100
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.NOT_CONFIRMED
    assert result.transition_event == "CLOSE_BACK_BELOW_RESISTANCE"


def test_breakout_attempt_severe_upper_wick_not_confirmed(bars_factory):
    # wick % ~96%, well above the 60% rejection threshold
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.5, 110, 100.2, 100.6))
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.NOT_CONFIRMED
    assert result.transition_event == "SEVERE_REJECTION_WICK"


def test_breakout_attempt_weak_candle_body_stays_in_state(bars_factory):
    # body_pct=25% (<40 threshold), wick=50% (<=60, not severe) — isolated case
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.5, 102, 100.0, 101))
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.BREAKOUT_ATTEMPT
    assert result.transition_event == "WEAK_CANDLE_BODY"


def test_breakout_attempt_good_candle_advances_to_candle_confirmation(bars_factory):
    # strong body (~90%), small wick (~7%)
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.2, 103, 100.1, 102.8))
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.CANDLE_CONFIRMATION
    assert result.transition_event == "CANDLE_QUALITY_OK"


def test_breakout_attempt_wick_rejection_takes_priority_over_weak_body(bars_factory):
    # a candle that is BOTH weak-bodied AND has a severe wick should reject
    # via SEVERE_REJECTION_WICK (checked first), not WEAK_CANDLE_BODY.
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.5, 110, 100.2, 100.6))
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, bars, [RESISTANCE])

    assert result.transition_event == "SEVERE_REJECTION_WICK"


def test_breakout_attempt_no_look_ahead(bars_factory):
    rows = [_bar_row(99, 99, 99, 99) for _ in range(4)]
    rows.append(_bar_row(100.2, 103, 100.1, 102.8))
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, bars_today, [RESISTANCE])

    future_rows = [_bar_row(1, 1, 1, 1) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(BreakoutState.BREAKOUT_ATTEMPT, truncated, [RESISTANCE])

    assert result_recomputed == result_today


# --- CANDLE_CONFIRMATION -----------------------------------------------------


def test_candle_confirmation_stays_when_rvol_unavailable(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)  # too few bars for RVOL (needs 21)

    result = advance_breakout_state(BreakoutState.CANDLE_CONFIRMATION, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.CANDLE_CONFIRMATION
    assert result.transition_event is None


def test_candle_confirmation_rvol_below_minimum_not_confirmed(flat_bars_factory):
    bars = flat_bars_factory(21, price=101.0, volume=100_000)  # today rvol == 1.0 < 1.5

    result = advance_breakout_state(BreakoutState.CANDLE_CONFIRMATION, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.NOT_CONFIRMED
    assert result.transition_event == "RVOL_BELOW_MINIMUM"


def test_candle_confirmation_rvol_confirmed_advances_to_volume_confirmation(bars_factory):
    rows = [_bar_row(101, 101, 101, 101, 100_000) for _ in range(20)]
    rows.append(_bar_row(101, 102, 100, 101, 300_000))  # rvol = 3.0
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.CANDLE_CONFIRMATION, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.VOLUME_CONFIRMATION
    assert result.transition_event == "RVOL_CONFIRMED"


def test_candle_confirmation_rvol_at_minimum_boundary_confirms(bars_factory):
    rows = [_bar_row(101, 101, 101, 101, 100_000) for _ in range(20)]
    rows.append(_bar_row(101, 102, 100, 101, 150_000))  # rvol == 1.5 exactly
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.CANDLE_CONFIRMATION, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.VOLUME_CONFIRMATION


def test_candle_confirmation_no_look_ahead(bars_factory):
    rows = [_bar_row(101, 101, 101, 101, 100_000) for _ in range(20)]
    rows.append(_bar_row(101, 102, 100, 101, 300_000))
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(BreakoutState.CANDLE_CONFIRMATION, bars_today, [RESISTANCE])

    future_rows = [_bar_row(1, 1, 1, 1, 1) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(BreakoutState.CANDLE_CONFIRMATION, truncated, [RESISTANCE])

    assert result_recomputed == result_today


# --- VOLUME_CONFIRMATION -----------------------------------------------------


def test_volume_confirmation_advances_to_confirmed_with_entry_stop_targets(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION, bars, [RESISTANCE], support_levels=[SUPPORT], atr=2.0
    )

    assert result.new_state == BreakoutState.CONFIRMED
    assert result.transition_event == "CONFIRMED"
    assert result.entry_price == pytest.approx(100.0 + 2.0 * 0.1)
    assert result.stop_loss == pytest.approx(95.0)
    assert result.targets is not None
    assert result.targets["target_1r"] == pytest.approx(result.entry_price + (result.entry_price - result.stop_loss))


def test_volume_confirmation_falls_back_to_flat_stop_when_no_support(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION, bars, [RESISTANCE], support_levels=None, atr=2.0
    )

    assert result.stop_loss == pytest.approx(101.0 * 0.97)


def test_volume_confirmation_caps_stop_loss_when_structural_support_is_too_far(flat_bars_factory):
    """A support level 50%+ below entry (e.g. an old base from before a big
    rally) is technically "the nearest structural support" but not a
    disciplined risk-management stop — capped at MAX_STOP_LOSS_PCT (10%)
    below entry instead. Found live 2026-08-17: uncapped stops as far as
    43% below entry were the real cause of a sub-1.0 profit factor despite
    a 62.5% win rate."""
    bars = flat_bars_factory(5, price=101.0)
    far_support = Level(level_type="SWING_LOW", price=50.0, strength=1.0)

    result = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION, bars, [RESISTANCE], support_levels=[far_support], atr=2.0
    )

    # cap is close * 0.9, not entry * 0.9 — _pick_stop_loss caps relative to
    # today's close, the same reference point the structural-support search
    # itself uses, not the (slightly different) ATR-buffered entry price.
    assert result.stop_loss == pytest.approx(101.0 * 0.9)
    assert result.stop_loss > 50.0  # the far support itself must not be used


def test_volume_confirmation_uses_tight_structural_support_when_within_cap(flat_bars_factory):
    """A structural support inside the cap is still preferred over the cap
    itself — the cap only binds when the structural level is too far."""
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION, bars, [RESISTANCE], support_levels=[SUPPORT], atr=2.0
    )

    assert result.stop_loss == pytest.approx(95.0)  # SUPPORT.price, tighter than the 10% cap


def test_volume_confirmation_target_ladder_includes_nearest_structural_target(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)
    far_resistance = Level(level_type="RESISTANCE_CLUSTER", price=120.0, strength=2.0)

    result = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION,
        bars,
        [RESISTANCE, far_resistance],
        support_levels=[SUPPORT],
        atr=2.0,
    )

    assert result.targets["nearest_structural_target"] == pytest.approx(120.0)


def test_volume_confirmation_no_look_ahead(bars_factory):
    rows = [_bar_row(101, 101, 101, 101) for _ in range(5)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION, bars_today, [RESISTANCE], support_levels=[SUPPORT], atr=2.0
    )

    future_rows = [_bar_row(200, 200, 200, 200) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(
        BreakoutState.VOLUME_CONFIRMATION, truncated, [RESISTANCE], support_levels=[SUPPORT], atr=2.0
    )

    assert result_recomputed == result_today


# --- CONFIRMED ---------------------------------------------------------------


def test_confirmed_advances_to_retest_pending(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(BreakoutState.CONFIRMED, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.RETEST_PENDING
    assert result.transition_event == "AWAITING_RETEST"


# --- RETEST_PENDING -----------------------------------------------------------


def test_retest_pending_invalidated_when_close_well_below_breakout_level(flat_bars_factory):
    bars = flat_bars_factory(5, price=97.0)  # >1.5% below 100

    result = advance_breakout_state(BreakoutState.RETEST_PENDING, bars, [RESISTANCE], bars_since_confirmed=1)

    assert result.new_state == BreakoutState.INVALIDATED
    assert result.transition_event == "CLOSE_BELOW_BREAKOUT_LEVEL"


def test_retest_pending_confirmed_when_window_elapsed(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(
        BreakoutState.RETEST_PENDING, bars, [RESISTANCE], bars_since_confirmed=RETEST_MAX_BARS
    )

    assert result.new_state == BreakoutState.RETEST_CONFIRMED
    assert result.transition_event == "RETEST_WINDOW_ELAPSED"


def test_retest_pending_confirmed_when_retest_held_near_level(flat_bars_factory):
    bars = flat_bars_factory(5, price=100.5)

    result = advance_breakout_state(BreakoutState.RETEST_PENDING, bars, [RESISTANCE], bars_since_confirmed=2)

    assert result.new_state == BreakoutState.RETEST_CONFIRMED
    assert result.transition_event == "RETEST_HELD"


def test_retest_pending_stays_when_near_but_below_breakout_level(bars_factory):
    # Resistance far above close so the resolved breakout_level stays at
    # 110 (nearest-by-distance, not the close itself); close is near
    # (within tolerance) but strictly below the breakout level, so
    # RETEST_HELD's `close >= breakout_level` condition fails.
    far_resistance = Level(level_type="RESISTANCE_CLUSTER", price=110.0, strength=2.0)
    rows = [_bar_row(109, 109, 109, 109) for _ in range(5)]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_breakout_state(BreakoutState.RETEST_PENDING, bars, [far_resistance], bars_since_confirmed=2)

    assert result.new_state == BreakoutState.RETEST_PENDING
    assert result.transition_event is None


def test_retest_pending_no_look_ahead(bars_factory):
    rows = [_bar_row(100.5, 100.5, 100.5, 100.5) for _ in range(5)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(
        BreakoutState.RETEST_PENDING, bars_today, [RESISTANCE], bars_since_confirmed=2
    )

    future_rows = [_bar_row(999, 999, 999, 999) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(
        BreakoutState.RETEST_PENDING, truncated, [RESISTANCE], bars_since_confirmed=2
    )

    assert result_recomputed == result_today


# --- RETEST_CONFIRMED ---------------------------------------------------------


def test_retest_confirmed_advances_to_trade_active(flat_bars_factory):
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(BreakoutState.RETEST_CONFIRMED, bars, [RESISTANCE])

    assert result.new_state == BreakoutState.TRADE_ACTIVE
    assert result.transition_event == "ENTRY_TRIGGERED"


# --- TRADE_ACTIVE / terminal passthrough -------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        BreakoutState.TRADE_ACTIVE,
        BreakoutState.TARGET_HIT,
        BreakoutState.INVALIDATED,
        BreakoutState.SESSION_END,
        BreakoutState.NOT_CONFIRMED,
    ],
)
def test_post_entry_and_terminal_states_pass_through_unchanged(flat_bars_factory, state):
    bars = flat_bars_factory(5, price=101.0)

    result = advance_breakout_state(state, bars, [RESISTANCE])

    assert result.new_state == state
    assert result.transition_event is None


# --- full-chain no-look-ahead across multiple representative states ---------


@pytest.mark.parametrize(
    "state",
    [BreakoutState.WATCH, BreakoutState.CANDLE_CONFIRMATION, BreakoutState.RETEST_PENDING],
)
def test_no_look_ahead_holds_across_representative_states(bars_factory, state):
    rows = [_bar_row(100, 101, 99, 100.5, 100_000) for _ in range(21)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_breakout_state(
        state, bars_today, [RESISTANCE], support_levels=[SUPPORT], atr=1.5, bars_since_confirmed=2
    )

    future_rows = [_bar_row(500, 600, 400, 550, 999_999) for _ in range(4)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated_to_today = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_breakout_state(
        state, truncated_to_today, [RESISTANCE], support_levels=[SUPPORT], atr=1.5, bars_since_confirmed=2
    )

    assert result_recomputed == result_today
