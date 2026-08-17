"""Tests for app.backtest.outcomes.

Coverage: empty bars_after_entry -> SESSION_END immediately, MFE/MAE
tracking, SL-before-target ordering on an ambiguous same-bar breach,
structural target checked ahead of the R-multiple ladder, R-multiple
ladder priority (3R > 2R > 1.5R > 1R per the source's actual target_order),
and the SESSION_END case where bars run out with the position still open
(must not be force-closed).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.backtest.outcomes import track_trade_outcome

ENTRY_DATE = date(2024, 1, 2)
ENTRY_PRICE = 100.0
STOP_LOSS = 90.0

TARGETS = {
    "target_1r": 110.0,
    "target_1_5r": 115.0,
    "target_2r": 120.0,
    "target_3r": 130.0,
    "nearest_structural_target": None,
}


def _bars(rows: list[dict]) -> pd.DataFrame:
    dates = pd.date_range(start="2024-01-03", periods=len(rows), freq="D")
    df = pd.DataFrame(rows, index=dates)
    return df


def test_empty_bars_after_entry_returns_session_end_immediately():
    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, pd.DataFrame())

    assert result.exit_reason == "SESSION_END"
    assert result.exit_price is None
    assert result.exit_date is None
    assert result.sl_hit is False
    assert result.target_hit is None
    assert result.mfe == 0.0
    assert result.mae == 0.0
    assert result.holding_days == 0


def test_mfe_mae_tracked_across_bars_before_exit():
    bars = _bars(
        [
            {"high": 103.0, "low": 98.0},
            {"high": 106.0, "low": 95.0},
            {"high": 104.0, "low": 99.0},
        ]
    )

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.mfe == pytest.approx(0.06)  # (best high 106 - entry 100) / entry 100
    assert result.mae == pytest.approx(-0.05)  # (worst low 95 - entry 100) / entry 100
    assert result.exit_reason == "SESSION_END"


def test_stop_loss_checked_before_target_on_same_ambiguous_bar():
    """A single bar whose low breaches SL and whose high also clears a
    target must resolve to SL, per the documented conservative assumption
    (intraday ordering within a daily bar is unknown)."""
    bars = _bars(
        [
            {"high": 135.0, "low": 85.0},  # clears 3R (130) AND breaches SL (90)
        ]
    )

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.sl_hit is True
    assert result.exit_reason == "INVALIDATED"
    assert result.target_hit is None
    assert result.exit_price == pytest.approx(STOP_LOSS)
    assert result.holding_days == 1


def test_stop_loss_hit_without_any_target_ambiguity():
    bars = _bars([{"high": 101.0, "low": 89.0}])

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.sl_hit is True
    assert result.exit_reason == "INVALIDATED"
    assert result.exit_price == pytest.approx(STOP_LOSS)


def test_exit_date_matches_the_bar_that_triggered_sl():
    bars = _bars(
        [
            {"high": 103.0, "low": 95.0},
            {"high": 104.0, "low": 88.0},  # SL breached here
        ]
    )

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.exit_reason == "INVALIDATED"
    assert result.holding_days == 2
    assert result.exit_date == date(2024, 1, 4)


def test_target_ladder_hits_3r_before_2r_when_high_clears_both():
    """Real source order is 3R, then 2R, then 1.5R, then 1R — the highest
    target is checked first, so a bar clearing multiple targets resolves to
    the highest one reached, not the lowest."""
    bars = _bars([{"high": 131.0, "low": 99.0}])  # clears 1R,1.5R,2R,3R all at once

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.target_hit == "3R"
    assert result.exit_reason == "TARGET_HIT"
    assert result.exit_price == pytest.approx(130.0)


def test_target_ladder_hits_2r_when_only_up_to_2r_cleared():
    bars = _bars([{"high": 121.0, "low": 99.0}])  # clears 1R,1.5R,2R but not 3R

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.target_hit == "2R"
    assert result.exit_price == pytest.approx(120.0)


def test_target_ladder_hits_1_5r_when_only_up_to_1_5r_cleared():
    bars = _bars([{"high": 116.0, "low": 99.0}])  # clears 1R,1.5R but not 2R

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.target_hit == "1.5R"
    assert result.exit_price == pytest.approx(115.0)


def test_target_ladder_hits_1r_when_only_1r_cleared():
    bars = _bars([{"high": 111.0, "low": 99.0}])  # clears only 1R

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.target_hit == "1R"
    assert result.exit_price == pytest.approx(110.0)


def test_structural_target_checked_ahead_of_r_multiple_ladder():
    """Source checks `structural` before looping target_order — a bar that
    clears both the structural target and an R-multiple must resolve to
    STRUCTURAL."""
    targets_with_structural = {**TARGETS, "nearest_structural_target": 108.0}
    bars = _bars([{"high": 111.0, "low": 99.0}])  # clears structural (108) AND 1R (110)... wait need both cleared

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, targets_with_structural, bars)

    assert result.target_hit == "STRUCTURAL"
    assert result.exit_price == pytest.approx(108.0)


def test_structural_target_wins_even_when_higher_r_multiple_also_cleared_same_bar():
    targets_with_structural = {**TARGETS, "nearest_structural_target": 105.0}
    bars = _bars([{"high": 131.0, "low": 99.0}])  # clears structural AND all R-multiples

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, targets_with_structural, bars)

    assert result.target_hit == "STRUCTURAL"
    assert result.exit_reason == "TARGET_HIT"
    assert result.exit_price == pytest.approx(105.0)


def test_none_structural_target_is_skipped_without_error():
    bars = _bars([{"high": 111.0, "low": 99.0}])

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.target_hit == "1R"


def test_missing_target_keys_treated_as_none_and_skipped():
    sparse_targets = {"target_1r": 110.0}
    bars = _bars([{"high": 111.0, "low": 99.0}])

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, sparse_targets, bars)

    assert result.target_hit == "1R"
    assert result.exit_reason == "TARGET_HIT"


def test_session_end_when_bars_run_out_with_position_still_open():
    bars = _bars(
        [
            {"high": 103.0, "low": 98.0},
            {"high": 105.0, "low": 97.0},
            {"high": 108.0, "low": 96.0},  # never reaches SL(90) or 1R(110)
        ]
    )

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.exit_reason == "SESSION_END"
    assert result.exit_price is None
    assert result.exit_date is None
    assert result.sl_hit is False
    assert result.target_hit is None
    assert result.holding_days == 3


def test_session_end_is_not_force_closed_at_last_bars_price():
    """Explicitly assert exit_price stays None on SESSION_END — proves the
    position is logged as still-open, not force-closed at the final bar's
    close/high/low."""
    bars = _bars([{"high": 109.9, "low": 90.1}])  # right at the edges, neither triggers

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.exit_reason == "SESSION_END"
    assert result.exit_price is None


def test_mfe_never_negative_and_mae_never_positive_with_favorable_only_bars():
    bars = _bars([{"high": 105.0, "low": 101.0}])

    result = track_trade_outcome(ENTRY_DATE, ENTRY_PRICE, STOP_LOSS, TARGETS, bars)

    assert result.mfe >= 0.0
    assert result.mae <= 0.0
