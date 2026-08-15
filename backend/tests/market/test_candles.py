"""Tests for app.market.candles.

NOTE ON API MISMATCH (partially resolved mid-session — see task report):
this module's consumers (app/breakout/breakout_engine.py,
app/breakout/dip_buy_engine.py, app/backtest/simulator.py) import
candle_quality, upper_wick_pct, and has_bullish_reversal from
app.market.candles. As of writing this test file, candle_quality and
upper_wick_pct now exist (added mid-session by another concurrent editor),
but has_bullish_reversal still does not — the module only exposes
detect_reversal_pattern(bars), so app/breakout/dip_buy_engine.py's `from
app.market.candles import has_bullish_reversal` still fails at import time.
These tests exercise the actual current public surface: candle_quality,
upper_wick_pct, detect_reversal_pattern, and (via module-level import) the
private pattern-matching helpers since that's the real logic under test.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.market.candles import (
    BULLISH_ENGULFING,
    HAMMER,
    MORNING_STAR,
    CandleQuality,
    _is_bullish_engulfing,
    _is_hammer,
    _is_morning_star,
    candle_quality,
    detect_reversal_pattern,
    upper_wick_pct,
)


def _bar(open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


# --- candle_quality ----------------------------------------------------------


def test_candle_quality_full_body_bar():
    # open == low, close == high -> full-range body, close at the top
    result = candle_quality(open_=100.0, high=110.0, low=100.0, close=110.0)

    assert isinstance(result, CandleQuality)
    assert result.body_pct == pytest.approx(100.0)
    assert result.close_location_pct == pytest.approx(100.0)


def test_candle_quality_doji_zero_body():
    result = candle_quality(open_=105.0, high=110.0, low=100.0, close=105.0)

    assert result.body_pct == pytest.approx(0.0)
    assert result.close_location_pct == pytest.approx(50.0)


def test_candle_quality_close_at_low():
    result = candle_quality(open_=110.0, high=110.0, low=100.0, close=100.0)

    assert result.close_location_pct == pytest.approx(0.0)


def test_candle_quality_zero_range_returns_neutral_defaults():
    result = candle_quality(open_=100.0, high=100.0, low=100.0, close=100.0)

    assert result.body_pct == pytest.approx(0.0)
    assert result.close_location_pct == pytest.approx(50.0)


def test_candle_quality_negative_range_treated_like_zero_range():
    # high < low shouldn't happen in real data, but guard against it dividing
    # by a negative full_range and returning a nonsensical/negative pct
    result = candle_quality(open_=100.0, high=90.0, low=100.0, close=95.0)

    assert result.body_pct == pytest.approx(0.0)
    assert result.close_location_pct == pytest.approx(50.0)


# --- upper_wick_pct ------------------------------------------------------


def test_upper_wick_pct_no_upper_wick_when_close_is_high():
    result = upper_wick_pct(open_=100.0, high=110.0, low=95.0, close=110.0)

    assert result == pytest.approx(0.0)


def test_upper_wick_pct_full_wick_when_body_at_bottom():
    result = upper_wick_pct(open_=95.0, high=110.0, low=95.0, close=95.0)

    assert result == pytest.approx(100.0)


def test_upper_wick_pct_severe_rejection_wick_above_60_pct():
    # body confined to bottom 30% of range -> upper wick is 70% of range
    result = upper_wick_pct(open_=100.0, high=110.0, low=100.0, close=103.0)

    assert result > 60.0


def test_upper_wick_pct_zero_range_returns_zero():
    result = upper_wick_pct(open_=100.0, high=100.0, low=100.0, close=100.0)

    assert result == pytest.approx(0.0)


def test_upper_wick_pct_uses_max_of_open_close_as_body_top():
    # bearish bar: open is the body top, not close
    result = upper_wick_pct(open_=108.0, high=110.0, low=100.0, close=101.0)

    expected = (110.0 - 108.0) / (110.0 - 100.0) * 100
    assert result == pytest.approx(expected)


# --- _is_hammer --------------------------------------------------------------


def test_is_hammer_true_for_classic_hammer():
    # small body near top, long lower wick, tiny/no upper wick
    bar = _bar(open_=100, high=101, low=90, close=100.5)

    assert _is_hammer(bar) is True


def test_is_hammer_false_when_body_is_large():
    bar = _bar(open_=90, high=101, low=89, close=100)

    assert _is_hammer(bar) is False


def test_is_hammer_false_when_upper_wick_too_large():
    bar = _bar(open_=95, high=110, low=90, close=96)

    assert _is_hammer(bar) is False


def test_is_hammer_false_when_zero_range():
    bar = _bar(open_=100, high=100, low=100, close=100)

    assert _is_hammer(bar) is False


def test_is_hammer_false_when_zero_body_doji():
    bar = _bar(open_=100, high=105, low=95, close=100)

    assert _is_hammer(bar) is False


# --- _is_bullish_engulfing -----------------------------------------------


def test_is_bullish_engulfing_true_for_classic_pattern():
    prev = _bar(open_=100, high=101, low=95, close=96)  # bearish
    curr = _bar(open_=95, high=105, low=94, close=101)  # bullish, engulfs

    assert _is_bullish_engulfing(prev, curr) is True


def test_is_bullish_engulfing_false_when_prev_bar_is_bullish():
    prev = _bar(open_=95, high=101, low=94, close=100)  # bullish
    curr = _bar(open_=99, high=105, low=98, close=104)

    assert _is_bullish_engulfing(prev, curr) is False


def test_is_bullish_engulfing_false_when_current_bar_is_bearish():
    prev = _bar(open_=100, high=101, low=95, close=96)
    curr = _bar(open_=97, high=98, low=90, close=91)  # bearish

    assert _is_bullish_engulfing(prev, curr) is False


def test_is_bullish_engulfing_false_when_does_not_fully_engulf():
    prev = _bar(open_=100, high=101, low=95, close=96)
    curr = _bar(open_=97, high=99, low=96, close=98)  # doesn't cover prev range

    assert _is_bullish_engulfing(prev, curr) is False


# --- _is_morning_star ---------------------------------------------------


def test_is_morning_star_true_for_classic_pattern():
    bar1 = _bar(open_=110, high=111, low=100, close=101)  # big bearish
    bar2 = _bar(open_=99, high=100, low=97, close=99.5)  # small body, gapped down
    bar3 = _bar(open_=100, high=112, low=99, close=110)  # bullish, closes above bar1 midpoint

    assert _is_morning_star(bar1, bar2, bar3) is True


def test_is_morning_star_false_when_first_bar_bullish():
    bar1 = _bar(open_=100, high=111, low=99, close=110)  # bullish, not bearish
    bar2 = _bar(open_=99, high=100, low=97, close=99.5)
    bar3 = _bar(open_=100, high=112, low=99, close=110)

    assert _is_morning_star(bar1, bar2, bar3) is False


def test_is_morning_star_false_when_third_bar_not_bullish():
    bar1 = _bar(open_=110, high=111, low=100, close=101)
    bar2 = _bar(open_=99, high=100, low=97, close=99.5)
    bar3 = _bar(open_=105, high=106, low=99, close=100)  # bearish

    assert _is_morning_star(bar1, bar2, bar3) is False


def test_is_morning_star_false_when_third_bar_does_not_recover_enough():
    bar1 = _bar(open_=110, high=111, low=100, close=101)
    bar2 = _bar(open_=99, high=100, low=97, close=99.5)
    bar3 = _bar(open_=100, high=103, low=99, close=102)  # bullish but below bar1 midpoint

    assert _is_morning_star(bar1, bar2, bar3) is False


def test_is_morning_star_false_when_middle_bar_body_too_large():
    bar1 = _bar(open_=110, high=111, low=100, close=101)
    bar2 = _bar(open_=105, high=106, low=95, close=96)  # large body, not small/doji
    bar3 = _bar(open_=100, high=112, low=99, close=110)

    assert _is_morning_star(bar1, bar2, bar3) is False


def test_is_morning_star_false_when_zero_range_bars():
    bar1 = _bar(open_=100, high=100, low=100, close=100)
    bar2 = _bar(open_=99, high=100, low=97, close=99.5)
    bar3 = _bar(open_=100, high=112, low=99, close=110)

    assert _is_morning_star(bar1, bar2, bar3) is False


# --- detect_reversal_pattern -------------------------------------------


def test_detect_reversal_pattern_empty_bars_returns_none():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = detect_reversal_pattern(bars)

    assert result == {"pattern": None, "bar_date": None}


def test_detect_reversal_pattern_single_bar_can_detect_hammer(bars_factory):
    bars = bars_factory(
        [{"open": 100, "high": 101, "low": 90, "close": 100.5, "volume": 1000}],
        start=date(2024, 1, 1),
    )

    result = detect_reversal_pattern(bars)

    assert result["pattern"] == HAMMER
    assert result["bar_date"] == bars.index[-1]


def test_detect_reversal_pattern_no_pattern_returns_none(flat_bars_factory):
    bars = flat_bars_factory(5, price=100.0)

    result = detect_reversal_pattern(bars)

    assert result == {"pattern": None, "bar_date": None}


def test_detect_reversal_pattern_detects_bullish_engulfing_with_two_bars(bars_factory):
    bars = bars_factory(
        [
            {"open": 100, "high": 101, "low": 95, "close": 96, "volume": 1000},  # bearish
            {"open": 95, "high": 105, "low": 94, "close": 101, "volume": 1000},  # engulfs
        ],
        start=date(2024, 1, 1),
    )

    result = detect_reversal_pattern(bars)

    assert result["pattern"] == BULLISH_ENGULFING
    assert result["bar_date"] == bars.index[-1]


def test_detect_reversal_pattern_detects_morning_star_with_three_bars(bars_factory):
    bars = bars_factory(
        [
            {"open": 110, "high": 111, "low": 100, "close": 101, "volume": 1000},
            {"open": 99, "high": 100, "low": 97, "close": 99.5, "volume": 1000},
            {"open": 100, "high": 112, "low": 99, "close": 110, "volume": 1000},
        ],
        start=date(2024, 1, 1),
    )

    result = detect_reversal_pattern(bars)

    assert result["pattern"] == MORNING_STAR
    assert result["bar_date"] == bars.index[-1]


def test_detect_reversal_pattern_prioritizes_morning_star_over_engulfing(bars_factory):
    """When both a 3-bar morning star and a 2-bar engulfing pattern would
    match on the same trailing bars, morning star is checked first in the
    function body — pin this priority order down explicitly.
    """
    bars = bars_factory(
        [
            {"open": 110, "high": 111, "low": 100, "close": 101, "volume": 1000},
            {"open": 99, "high": 100, "low": 97, "close": 99.5, "volume": 1000},  # bearish small body -> prev not bullish, so engulfing check on (bar2, bar3) may also apply
            {"open": 100, "high": 112, "low": 99, "close": 110, "volume": 1000},
        ],
        start=date(2024, 1, 1),
    )

    result = detect_reversal_pattern(bars)

    assert result["pattern"] == MORNING_STAR


def test_detect_reversal_pattern_only_evaluates_last_bar_as_candidate(bars_factory):
    # A hammer earlier in history should not be reported once later,
    # non-hammer bars are appended.
    bars = bars_factory(
        [
            {"open": 100, "high": 101, "low": 90, "close": 100.5, "volume": 1000},  # hammer (ignored, not last)
            {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000},
            {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000},
        ],
        start=date(2024, 1, 1),
    )

    result = detect_reversal_pattern(bars)

    assert result["pattern"] is None


# --- No-look-ahead invariant -----------------------------------------------


def test_no_look_ahead_detect_reversal_pattern_stable_at_truncation_point(bars_factory):
    rows = [
        {"open": 110, "high": 111, "low": 100, "close": 101, "volume": 1000},
        {"open": 99, "high": 100, "low": 97, "close": 99.5, "volume": 1000},
        {"open": 100, "high": 112, "low": 99, "close": 110, "volume": 1000},
    ]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = detect_reversal_pattern(bars_today)

    future_rows = [{"open": 200, "high": 205, "low": 195, "close": 202, "volume": 1000} for _ in range(4)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = detect_reversal_pattern(truncated)

    assert result_recomputed == result_today


def test_no_look_ahead_hammer_detection_unaffected_by_future_bars(bars_factory):
    rows = [{"open": 100, "high": 101, "low": 90, "close": 100.5, "volume": 1000}]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))
    result_today = detect_reversal_pattern(bars_today)

    future_rows = [{"open": 300, "high": 301, "low": 299, "close": 300.5, "volume": 1000} for _ in range(3)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = detect_reversal_pattern(truncated)

    assert result_recomputed == result_today
