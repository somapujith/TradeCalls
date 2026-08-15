"""Tests for app.breakout.dip_buy_engine.advance_dip_buy_state.

Covers every state transition in the UPTREND_CONFIRMED -> ... ->
TRADE_ACTIVE chain, the uptrend precondition (observable via
UPTREND_CONFIRMED's reject-to-NOT_CONFIRMED behavior), the stop-loss ==
pullback_low design choice on CONFIRMED (per docs/engine.md: "stop-loss
below the dip's low ... not a wider structural stop"), and the
no-look-ahead invariant.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.breakout.dip_buy_engine import (
    MIN_RVOL_CONFIRM,
    RETEST_MAX_BARS,
    SUPPORT_TOLERANCE_PCT,
    advance_dip_buy_state,
)
from app.breakout.states import DipBuyState
from app.market.levels import Level

SUPPORT = Level(level_type="SUPPORT_CLUSTER", price=95.0, strength=2.0)
RESISTANCE = Level(level_type="RESISTANCE_CLUSTER", price=105.0, strength=2.0)


def _bar_row(open_, high, low, close, volume=100_000):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


# --- empty history -------------------------------------------------------


def test_empty_bar_history_returns_current_state_unchanged():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, empty, [])

    assert result.new_state == DipBuyState.UPTREND_CONFIRMED
    assert result.transition_event is None


# --- UPTREND_CONFIRMED precondition (docs/engine.md dip-buy precondition) --


def test_uptrend_precondition_fails_when_ema_missing(flat_bars_factory):
    bars = flat_bars_factory(10, price=100.0)

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [])

    assert result.new_state == DipBuyState.NOT_CONFIRMED
    assert result.transition_event == "UPTREND_PRECONDITION_FAILED"


def test_uptrend_precondition_fails_when_price_below_ema50(flat_bars_factory):
    bars = flat_bars_factory(10, price=100.0)

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [], ema20=105, ema50=110)

    assert result.new_state == DipBuyState.NOT_CONFIRMED
    assert result.transition_event == "UPTREND_PRECONDITION_FAILED"


def test_uptrend_precondition_fails_when_price_equals_ema50(flat_bars_factory):
    # close <= ema50 is the guard (boundary inclusive)
    bars = flat_bars_factory(10, price=100.0)

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [], ema20=101, ema50=100)

    assert result.new_state == DipBuyState.NOT_CONFIRMED


def test_uptrend_precondition_fails_when_ema20_at_or_below_ema50(flat_bars_factory):
    bars = flat_bars_factory(10, price=100.0)

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [], ema20=95, ema50=99)

    assert result.new_state == DipBuyState.NOT_CONFIRMED
    assert result.transition_event == "UPTREND_PRECONDITION_FAILED"


def test_uptrend_precondition_fails_on_lower_low_swing_structure(bars_factory):
    # Two swing lows, second lower than first -> downtrend continuation,
    # not a dip, even though price/EMA conditions otherwise pass.
    lows = [100, 99, 98, 90, 98, 99, 100, 99, 98, 85, 98, 99, 100]
    rows = [_bar_row(lo + 3, lo + 5, lo, lo + 3) for lo in lows]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [], ema20=105, ema50=90)

    assert result.new_state == DipBuyState.NOT_CONFIRMED
    assert result.transition_event == "UPTREND_PRECONDITION_FAILED"


def test_uptrend_precondition_passes_stays_uptrend_confirmed_when_flat(flat_bars_factory):
    bars = flat_bars_factory(10, price=100.0)

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [], ema20=105, ema50=95)

    assert result.new_state == DipBuyState.UPTREND_CONFIRMED
    assert result.transition_event is None


def test_uptrend_confirmed_advances_to_pullback_when_close_drops(bars_factory):
    rows = [_bar_row(100, 102, 99, 101) for _ in range(9)]
    rows.append(_bar_row(100, 101, 98, 99))  # close(99) < prior close(101)
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars, [], ema20=100, ema50=90)

    assert result.new_state == DipBuyState.PULLBACK_IN_PROGRESS
    assert result.transition_event == "PULLBACK_STARTED"


def test_uptrend_confirmed_no_look_ahead(bars_factory):
    rows = [_bar_row(100, 101, 99, 100) for _ in range(10)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(DipBuyState.UPTREND_CONFIRMED, bars_today, [], ema20=105, ema50=95)

    future_rows = [_bar_row(500, 500, 500, 500) for _ in range(5)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(
        DipBuyState.UPTREND_CONFIRMED, truncated, [], ema20=105, ema50=95
    )

    assert result_recomputed == result_today


# --- PULLBACK_IN_PROGRESS -----------------------------------------------


def test_pullback_in_progress_invalidated_when_trend_breaks(flat_bars_factory):
    bars = flat_bars_factory(10, price=100.0)

    # no ema supplied -> precondition fails -> trend considered broken
    result = advance_dip_buy_state(DipBuyState.PULLBACK_IN_PROGRESS, bars, [SUPPORT])

    assert result.new_state == DipBuyState.INVALIDATED
    assert result.transition_event == "TREND_BROKEN_DURING_PULLBACK"


def test_pullback_in_progress_stays_when_no_support_nearby(flat_bars_factory):
    bars = flat_bars_factory(10, price=100.0)  # far from support at 95

    result = advance_dip_buy_state(DipBuyState.PULLBACK_IN_PROGRESS, bars, [SUPPORT], ema20=101, ema50=90)

    assert result.new_state == DipBuyState.PULLBACK_IN_PROGRESS
    assert result.transition_event is None


def test_pullback_in_progress_stays_when_no_support_clusters_at_all(flat_bars_factory):
    # empty support_clusters -> _nearest_support returns None outright
    bars = flat_bars_factory(10, price=100.0)

    result = advance_dip_buy_state(DipBuyState.PULLBACK_IN_PROGRESS, bars, [], ema20=101, ema50=90)

    assert result.new_state == DipBuyState.PULLBACK_IN_PROGRESS
    assert result.transition_event is None


def test_pullback_in_progress_advances_to_support_test_when_near_support(flat_bars_factory):
    bars = flat_bars_factory(10, price=95.3)  # within SUPPORT_TOLERANCE_PCT of 95.0

    result = advance_dip_buy_state(DipBuyState.PULLBACK_IN_PROGRESS, bars, [SUPPORT], ema20=101, ema50=90)

    assert result.new_state == DipBuyState.SUPPORT_TEST
    assert result.transition_event == "PRICE_AT_SUPPORT"


def test_pullback_in_progress_no_look_ahead(bars_factory):
    rows = [_bar_row(100, 101, 99, 100) for _ in range(10)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(
        DipBuyState.PULLBACK_IN_PROGRESS, bars_today, [SUPPORT], ema20=105, ema50=95
    )

    future_rows = [_bar_row(1, 1, 1, 1) for _ in range(4)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(
        DipBuyState.PULLBACK_IN_PROGRESS, truncated, [SUPPORT], ema20=105, ema50=95
    )

    assert result_recomputed == result_today


# --- SUPPORT_TEST -------------------------------------------------------


def test_support_test_closed_below_support_not_confirmed(bars_factory):
    # close(92.5) well below support(95.0) by more than SUPPORT_TOLERANCE_PCT
    rows = [_bar_row(95, 96, 94, 95.3) for _ in range(2)]
    rows.append(_bar_row(94, 94.5, 92, 92.5))
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.SUPPORT_TEST, bars, [SUPPORT])

    assert result.new_state == DipBuyState.NOT_CONFIRMED
    assert result.transition_event == "SUPPORT_CLOSED_BELOW"


def test_support_test_stays_when_no_reversal_pattern_yet(bars_factory):
    rows = [_bar_row(95, 96, 94, 95.3) for _ in range(2)]
    rows.append(_bar_row(95.2, 95.5, 95.0, 95.1))  # tiny indecisive bar, no pattern
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.SUPPORT_TEST, bars, [SUPPORT])

    assert result.new_state == DipBuyState.SUPPORT_TEST
    assert result.transition_event is None


def test_support_test_advances_to_reversal_candle_on_hammer_closing_above_support(bars_factory):
    rows = [_bar_row(95, 96, 94, 95.3) for _ in range(2)]
    rows.append(_bar_row(95.5, 95.7, 93.0, 95.4))  # hammer, close(95.4) > support(95.0)
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.SUPPORT_TEST, bars, [SUPPORT])

    assert result.new_state == DipBuyState.REVERSAL_CANDLE
    assert result.transition_event == "REVERSAL_PATTERN_HAMMER"


def test_support_test_stays_when_pattern_found_but_close_still_below_support(bars_factory):
    # A hammer-shaped bar whose close hasn't recovered back above support
    # yet needs the "close back above support" gate to hold it in SUPPORT_TEST.
    rows = [_bar_row(95, 96, 94, 95.3) for _ in range(2)]
    rows.append(_bar_row(95.0, 95.2, 93.0, 94.9))  # close(94.9) < support(95.0)
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.SUPPORT_TEST, bars, [SUPPORT])

    assert result.new_state == DipBuyState.SUPPORT_TEST
    assert result.transition_event is None


def test_support_test_no_look_ahead(bars_factory):
    rows = [_bar_row(95, 96, 94, 95.3) for _ in range(2)]
    rows.append(_bar_row(95.5, 95.7, 93.0, 95.4))
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(DipBuyState.SUPPORT_TEST, bars_today, [SUPPORT])

    future_rows = [_bar_row(200, 200, 200, 200) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(DipBuyState.SUPPORT_TEST, truncated, [SUPPORT])

    assert result_recomputed == result_today


# --- REVERSAL_CANDLE ------------------------------------------------------


def test_reversal_candle_stays_when_rvol_unavailable(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.0)  # too few bars for RVOL

    result = advance_dip_buy_state(DipBuyState.REVERSAL_CANDLE, bars, [])

    assert result.new_state == DipBuyState.REVERSAL_CANDLE
    assert result.transition_event is None


def test_reversal_candle_rvol_below_minimum_not_confirmed(bars_factory):
    rows = [_bar_row(95, 96, 94, 95) for _ in range(20)]
    rows.append(_bar_row(95, 96, 94, 95.5, volume=100_000))  # rvol == 1.0 < 1.3
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.REVERSAL_CANDLE, bars, [])

    assert result.new_state == DipBuyState.NOT_CONFIRMED
    assert result.transition_event == "REVERSAL_RVOL_BELOW_MINIMUM"


def test_reversal_candle_rvol_confirmed_advances_to_volume_confirmation(bars_factory):
    rows = [_bar_row(95, 96, 94, 95) for _ in range(20)]
    rows.append(_bar_row(95, 96, 94, 95.5, volume=150_000))  # rvol == 1.5 >= 1.3
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.REVERSAL_CANDLE, bars, [])

    assert result.new_state == DipBuyState.VOLUME_CONFIRMATION
    assert result.transition_event == "RVOL_CONFIRMED"


def test_reversal_candle_min_rvol_is_lower_than_breakout_engine(bars_factory):
    # dip-buy's RVOL bar (1.3) is documented as lower than breakout's (1.5)
    # — reversal off a pullback, not a breakout surge.
    assert MIN_RVOL_CONFIRM == 1.3

    rows = [_bar_row(95, 96, 94, 95) for _ in range(20)]
    rows.append(_bar_row(95, 96, 94, 95.5, volume=135_000))  # rvol == 1.35
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(DipBuyState.REVERSAL_CANDLE, bars, [])

    assert result.new_state == DipBuyState.VOLUME_CONFIRMATION


def test_reversal_candle_no_look_ahead(bars_factory):
    rows = [_bar_row(95, 96, 94, 95) for _ in range(20)]
    rows.append(_bar_row(95, 96, 94, 95.5, volume=150_000))
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(DipBuyState.REVERSAL_CANDLE, bars_today, [])

    future_rows = [_bar_row(1, 1, 1, 1, 1) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(DipBuyState.REVERSAL_CANDLE, truncated, [])

    assert result_recomputed == result_today


# --- VOLUME_CONFIRMATION -> CONFIRMED (stop-loss design choice) -------------


def test_volume_confirmation_advances_to_confirmed_with_entry_stop_targets(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(
        DipBuyState.VOLUME_CONFIRMATION, bars, [], resistance_clusters=[RESISTANCE], atr=1.0, pullback_low=93.0
    )

    assert result.new_state == DipBuyState.CONFIRMED
    assert result.transition_event == "CONFIRMED"
    assert result.entry_price == pytest.approx(95.5 + 1.0 * 0.05)


def test_confirmed_stop_loss_equals_pullback_low_exactly_not_wider_structural_stop(flat_bars_factory):
    """docs/engine.md: "stop-loss = below the dip's low (the lowest point
    of the pullback, not a wider structural stop -- a dip-buy that
    revisits its own low has failed)". Verify stop == pullback_low
    exactly, regardless of any structural support levels supplied.
    """
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(
        DipBuyState.VOLUME_CONFIRMATION,
        bars,
        support_clusters=[Level(level_type="SUPPORT_CLUSTER", price=80.0, strength=3.0)],  # much wider/lower
        resistance_clusters=[RESISTANCE],
        atr=1.0,
        pullback_low=93.0,
    )

    assert result.stop_loss == 93.0  # exact equality — not derived from the 80.0 structural support


def test_volume_confirmation_falls_back_to_min_of_trailing_lows_without_pullback_low(bars_factory):
    rows = [_bar_row(95, 96, 94.0, 95.5) for _ in range(4)]
    rows.append(_bar_row(95, 96, 90.0, 95.5))  # lowest low in trailing window = 90.0
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = advance_dip_buy_state(
        DipBuyState.VOLUME_CONFIRMATION, bars, [], resistance_clusters=[RESISTANCE], atr=1.0, pullback_low=None
    )

    assert result.stop_loss == pytest.approx(90.0)


def test_volume_confirmation_target_ladder_includes_nearest_structural_target(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(
        DipBuyState.VOLUME_CONFIRMATION, bars, [], resistance_clusters=[RESISTANCE], atr=1.0, pullback_low=93.0
    )

    assert result.targets["nearest_structural_target"] == pytest.approx(105.0)
    risk = result.entry_price - result.stop_loss
    assert result.targets["target_1r"] == pytest.approx(result.entry_price + risk)
    assert result.targets["target_2r"] == pytest.approx(result.entry_price + 2 * risk)


def test_volume_confirmation_no_look_ahead(bars_factory):
    rows = [_bar_row(95, 96, 94, 95.5) for _ in range(5)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(
        DipBuyState.VOLUME_CONFIRMATION, bars_today, [], resistance_clusters=[RESISTANCE], atr=1.0, pullback_low=93.0
    )

    future_rows = [_bar_row(200, 200, 200, 200) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(
        DipBuyState.VOLUME_CONFIRMATION, truncated, [], resistance_clusters=[RESISTANCE], atr=1.0, pullback_low=93.0
    )

    assert result_recomputed == result_today


# --- CONFIRMED -------------------------------------------------------------


def test_confirmed_advances_to_retest_pending(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(DipBuyState.CONFIRMED, bars, [])

    assert result.new_state == DipBuyState.RETEST_PENDING
    assert result.transition_event == "AWAITING_RETEST"


# --- RETEST_PENDING ----------------------------------------------------


def test_retest_pending_invalidated_on_new_low_below_dip(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)  # flat bars low == 95.5

    result = advance_dip_buy_state(DipBuyState.RETEST_PENDING, bars, [], pullback_low=96.0)

    assert result.new_state == DipBuyState.INVALIDATED
    assert result.transition_event == "NEW_LOW_BELOW_DIP"


def test_retest_pending_confirmed_when_window_elapsed(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(
        DipBuyState.RETEST_PENDING, bars, [], pullback_low=90.0, bars_since_confirmed=RETEST_MAX_BARS
    )

    assert result.new_state == DipBuyState.RETEST_CONFIRMED
    assert result.transition_event == "RETEST_WINDOW_ELAPSED"


def test_retest_pending_stays_when_not_elapsed_and_no_new_low(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(
        DipBuyState.RETEST_PENDING, bars, [], pullback_low=90.0, bars_since_confirmed=2
    )

    assert result.new_state == DipBuyState.RETEST_PENDING
    assert result.transition_event is None


def test_retest_pending_no_look_ahead(bars_factory):
    rows = [_bar_row(95, 96, 94, 95.5) for _ in range(5)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(
        DipBuyState.RETEST_PENDING, bars_today, [], pullback_low=90.0, bars_since_confirmed=2
    )

    future_rows = [_bar_row(1, 1, 1, 1) for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(
        DipBuyState.RETEST_PENDING, truncated, [], pullback_low=90.0, bars_since_confirmed=2
    )

    assert result_recomputed == result_today


# --- RETEST_CONFIRMED --------------------------------------------------------


def test_retest_confirmed_advances_to_trade_active(flat_bars_factory):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(DipBuyState.RETEST_CONFIRMED, bars, [])

    assert result.new_state == DipBuyState.TRADE_ACTIVE
    assert result.transition_event == "ENTRY_TRIGGERED"


# --- TRADE_ACTIVE / terminal passthrough -------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        DipBuyState.TRADE_ACTIVE,
        DipBuyState.TARGET_HIT,
        DipBuyState.INVALIDATED,
        DipBuyState.SESSION_END,
        DipBuyState.NOT_CONFIRMED,
    ],
)
def test_post_entry_and_terminal_states_pass_through_unchanged(flat_bars_factory, state):
    bars = flat_bars_factory(5, price=95.5)

    result = advance_dip_buy_state(state, bars, [])

    assert result.new_state == state
    assert result.transition_event is None


# --- full-chain no-look-ahead across multiple representative states ---------


@pytest.mark.parametrize(
    "state",
    [DipBuyState.UPTREND_CONFIRMED, DipBuyState.REVERSAL_CANDLE, DipBuyState.RETEST_PENDING],
)
def test_no_look_ahead_holds_across_representative_states(bars_factory, state):
    rows = [_bar_row(100, 101, 99, 100.2, 100_000) for _ in range(21)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = advance_dip_buy_state(
        state,
        bars_today,
        [SUPPORT],
        resistance_clusters=[RESISTANCE],
        ema20=105,
        ema50=95,
        atr=1.2,
        pullback_low=90.0,
        bars_since_confirmed=2,
    )

    future_rows = [_bar_row(500, 600, 400, 550, 999_999) for _ in range(4)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated_to_today = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = advance_dip_buy_state(
        state,
        truncated_to_today,
        [SUPPORT],
        resistance_clusters=[RESISTANCE],
        ema20=105,
        ema50=95,
        atr=1.2,
        pullback_low=90.0,
        bars_since_confirmed=2,
    )

    assert result_recomputed == result_today
