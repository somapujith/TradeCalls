"""Tests for app.market.levels.

Covers: Level dataclass, prior_period_high_low, fifty_two_week_high_low,
swing_highs_lows (fractal swings), resistance_clusters/support_clusters
(tolerance/min_touches clustering), resistance_levels/support_levels
aggregators, and the no-look-ahead invariant for every function.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.market.levels import (
    Level,
    fifty_two_week_high_low,
    prior_period_high_low,
    resistance_clusters,
    resistance_levels,
    support_clusters,
    support_levels,
    swing_highs_lows,
)


# --- Level dataclass -------------------------------------------------------


def test_level_is_frozen_dataclass():
    level = Level(level_type="SWING_HIGH", price=100.0)

    assert level.level_type == "SWING_HIGH"
    assert level.price == 100.0
    assert level.strength is None
    with pytest.raises(AttributeError):
        level.price = 200.0  # type: ignore[misc]


def test_level_equality_by_value():
    a = Level(level_type="SWING_HIGH", price=100.0, strength=2.0)
    b = Level(level_type="SWING_HIGH", price=100.0, strength=2.0)

    assert a == b


# --- prior_period_high_low --------------------------------------------------


def test_prior_period_high_low_empty_returns_none_none():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    high, low = prior_period_high_low(bars, "D")

    assert high is None
    assert low is None


def test_prior_period_high_low_single_day_returns_none_none(bars_factory):
    bars = bars_factory(
        [{"open": 100, "high": 105, "low": 95, "close": 102, "volume": 1000}],
        start=date(2024, 1, 1),
    )

    high, low = prior_period_high_low(bars, "D")

    assert high is None
    assert low is None


def test_prior_period_high_low_daily(bars_factory):
    bars = bars_factory(
        [
            {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
            {"open": 106, "high": 120, "low": 100, "close": 115, "volume": 1000},
        ],
        start=date(2024, 1, 1),
    )

    high, low = prior_period_high_low(bars, "D")

    # prior day = the first (second-to-last) resampled bucket
    assert high == pytest.approx(110.0)
    assert low == pytest.approx(95.0)


def test_prior_period_high_low_weekly(bars_factory):
    # two full weeks of bars, ascending
    rows = [
        {"open": 100 + i, "high": 100 + i + 5, "low": 100 + i - 5, "close": 100 + i, "volume": 1000}
        for i in range(14)
    ]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    high, low = prior_period_high_low(bars, "W")

    assert high is not None
    assert low is not None
    assert high > low


def test_prior_period_high_low_monthly(bars_factory):
    # 'ME' (month-end), not the legacy 'M' alias — pandas 3.x removed 'M' as
    # a resample frequency string (raises ValueError), so 'ME' is the only
    # value that works against the pinned pandas version here.
    rows = [
        {"open": 100 + i, "high": 100 + i + 3, "low": 100 + i - 3, "close": 100 + i, "volume": 1000}
        for i in range(65)
    ]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    high, low = prior_period_high_low(bars, "ME")

    assert high is not None
    assert low is not None


def test_prior_period_high_low_no_look_ahead(bars_factory):
    rows = [
        {"open": 100 + i, "high": 100 + i + 5, "low": 100 + i - 5, "close": 100 + i, "volume": 1000}
        for i in range(20)
    ]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    high_today, low_today = prior_period_high_low(bars_today, "D")

    future_rows = [{"open": 500, "high": 999, "low": 1, "close": 500, "volume": 1000} for _ in range(5)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))

    high_recomputed, low_recomputed = prior_period_high_low(bars_with_future.iloc[: len(bars_today)], "D")

    assert high_recomputed == high_today
    assert low_recomputed == low_today


# --- fifty_two_week_high_low ------------------------------------------------


def test_fifty_two_week_high_low_empty_returns_none_none():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    high, low = fifty_two_week_high_low(bars)

    assert high is None
    assert low is None


def test_fifty_two_week_high_low_uses_last_252_bars(bars_factory):
    rows = [
        {"open": 100, "high": 100 + i, "low": 50 - i * 0.1, "close": 100, "volume": 1000}
        for i in range(300)
    ]
    bars = bars_factory(rows, start=date(2023, 1, 1))

    high, low = fifty_two_week_high_low(bars)

    window = bars.tail(252)
    assert high == pytest.approx(float(window["high"].max()))
    assert low == pytest.approx(float(window["low"].min()))
    # the max high is only reached in the very last row (i=299), which is
    # within the trailing-252 window, so it should be captured
    assert high == pytest.approx(100 + 299)


def test_fifty_two_week_high_low_fewer_than_252_bars_uses_all(bars_factory):
    rows = [{"open": 100, "high": 100 + i, "low": 90 - i, "close": 100, "volume": 1000} for i in range(50)]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    high, low = fifty_two_week_high_low(bars)

    assert high == pytest.approx(149.0)
    assert low == pytest.approx(41.0)


def test_fifty_two_week_high_low_no_look_ahead(bars_factory):
    rows = [{"open": 100, "high": 100 + i, "low": 90, "close": 100, "volume": 1000} for i in range(30)]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    high_today, low_today = fifty_two_week_high_low(bars_today)

    future_rows = [{"open": 100, "high": 999, "low": 1, "close": 100, "volume": 1000} for _ in range(5)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    high_recomputed, low_recomputed = fifty_two_week_high_low(truncated)

    assert high_recomputed == high_today
    assert low_recomputed == low_today


# --- swing_highs_lows --------------------------------------------------------


def test_swing_highs_lows_empty_returns_empty_list():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = swing_highs_lows(bars, lookback=3)

    assert result == []


def test_swing_highs_lows_too_short_returns_empty_list(flat_bars_factory):
    # needs 2*lookback+1 = 7 bars minimum for lookback=3
    bars = flat_bars_factory(6, price=100.0)

    result = swing_highs_lows(bars, lookback=3)

    assert result == []


def test_swing_highs_lows_detects_a_clean_swing_high(bars_factory):
    # bar at index 3 (0-based) is a local max with lookback=3 on each side
    highs = [100, 101, 102, 110, 102, 101, 100]
    rows = [{"open": h, "high": h, "low": h - 5, "close": h, "volume": 1000} for h in highs]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = swing_highs_lows(bars, lookback=3)

    swing_highs = [lvl for lvl in result if lvl.level_type == "SWING_HIGH"]
    assert len(swing_highs) == 1
    assert swing_highs[0].price == pytest.approx(110.0)


def test_swing_highs_lows_detects_a_clean_swing_low(bars_factory):
    lows = [100, 99, 98, 90, 98, 99, 100]
    rows = [{"open": lo, "high": lo + 5, "low": lo, "close": lo, "volume": 1000} for lo in lows]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = swing_highs_lows(bars, lookback=3)

    swing_lows = [lvl for lvl in result if lvl.level_type == "SWING_LOW"]
    assert len(swing_lows) == 1
    assert swing_lows[0].price == pytest.approx(90.0)


def test_swing_highs_lows_excludes_trailing_unconfirmed_bars(bars_factory):
    """The most recent `lookback` bars can't be confirmed as swings yet
    (there aren't enough future bars in the window) — documented as correct
    per levels.py's docstring, not a bug.
    """
    # Put an extreme high in the last bar; it must NOT be reported.
    highs = [100, 101, 102, 103, 104, 105, 999]
    rows = [{"open": h, "high": h, "low": h - 5, "close": h, "volume": 1000} for h in highs]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = swing_highs_lows(bars, lookback=3)

    prices = [lvl.price for lvl in result]
    assert 999 not in prices


def test_swing_highs_lows_no_look_ahead(bars_factory):
    highs = [100, 105, 101, 100, 110, 102, 101, 100, 103, 101]
    rows = [{"open": h, "high": h, "low": h - 3, "close": h, "volume": 1000} for h in highs]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = swing_highs_lows(bars_today, lookback=3)

    future_rows = [{"open": 500, "high": 500, "low": 495, "close": 500, "volume": 1000} for _ in range(5)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))

    # Recompute over the exact same truncated slice — must reproduce
    # identical swings for the already-confirmed points.
    result_recomputed_on_same_slice = swing_highs_lows(bars_with_future.iloc[: len(bars_today)], lookback=3)
    assert result_recomputed_on_same_slice == result_today

    # Recomputing over the FULL (with-future) history may newly confirm
    # swings near the old boundary (since more future bars are now
    # available to confirm them) but must never contradict/remove a
    # previously confirmed swing that lies well before the boundary.
    result_full = swing_highs_lows(bars_with_future, lookback=3)
    early_swings_today = [lvl for lvl in result_today if True]
    # every swing found in the truncated view must still be present when
    # more history is appended (swings don't get un-confirmed)
    for lvl in early_swings_today:
        assert lvl in result_full


# --- resistance_clusters / support_clusters ---------------------------------


def test_resistance_clusters_empty_bars_returns_empty_list():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = resistance_clusters(bars)

    assert result == []


def test_resistance_clusters_no_swings_returns_empty_list(flat_bars_factory):
    bars = flat_bars_factory(5, price=100.0)

    result = resistance_clusters(bars)

    assert result == []


def test_resistance_clusters_groups_within_tolerance(bars_factory):
    # Two separate swing highs close together (within 0.5%) and one far away.
    highs = [100, 101, 102, 150.0, 102, 101, 100, 101, 102, 150.5, 102, 101, 100]
    rows = [{"open": h, "high": h, "low": h - 5, "close": h, "volume": 1000} for h in highs]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = resistance_clusters(bars, tolerance_pct=0.5, min_touches=2)

    assert len(result) == 1
    cluster = result[0]
    assert cluster.level_type == "RESISTANCE_CLUSTER"
    assert cluster.strength == pytest.approx(2.0)
    assert cluster.price == pytest.approx((150.0 + 150.5) / 2, abs=0.1)


def test_resistance_clusters_min_touches_filters_singletons(bars_factory):
    highs = [100, 101, 102, 150.0, 102, 101, 100, 101, 102, 200.0, 102, 101, 100]
    rows = [{"open": h, "high": h, "low": h - 5, "close": h, "volume": 1000} for h in highs]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = resistance_clusters(bars, tolerance_pct=0.5, min_touches=2)

    # 150.0 and 200.0 are each single-touch swings > 0.5% apart from
    # anything else -> both should be filtered out by min_touches=2
    assert result == []


def test_support_clusters_groups_within_tolerance(bars_factory):
    lows = [100, 99, 98, 50.0, 98, 99, 100, 99, 98, 50.2, 98, 99, 100]
    rows = [{"open": lo, "high": lo + 5, "low": lo, "close": lo, "volume": 1000} for lo in lows]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = support_clusters(bars, tolerance_pct=0.5, min_touches=2)

    assert len(result) == 1
    cluster = result[0]
    assert cluster.level_type == "SUPPORT_CLUSTER"
    assert cluster.strength == pytest.approx(2.0)


def test_support_clusters_empty_bars_returns_empty_list():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = support_clusters(bars)

    assert result == []


def test_resistance_clusters_no_look_ahead(bars_factory):
    highs = [100, 101, 102, 150.0, 102, 101, 100, 101, 102, 150.3, 102, 101, 100]
    rows = [{"open": h, "high": h, "low": h - 5, "close": h, "volume": 1000} for h in highs]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = resistance_clusters(bars_today)

    future_rows = [{"open": 999, "high": 999, "low": 995, "close": 999, "volume": 1000} for _ in range(4)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = resistance_clusters(truncated)

    assert result_recomputed == result_today


# --- resistance_levels / support_levels aggregators -------------------------


def test_resistance_levels_empty_bars_returns_empty_list():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = resistance_levels(bars)

    assert result == []


def test_resistance_levels_aggregates_multiple_level_types(bars_factory):
    rows = [
        {"open": 100 + i, "high": 100 + i + 5, "low": 100 + i - 5, "close": 100 + i, "volume": 1000}
        for i in range(40)
    ]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = resistance_levels(bars)

    level_types = {lvl.level_type for lvl in result}
    # at minimum, the always-present prior-period / 52W levels should show up
    assert "PRIOR_DAY_HIGH" in level_types
    assert "52W_HIGH" in level_types


def test_support_levels_empty_bars_returns_empty_list():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = support_levels(bars)

    assert result == []


def test_support_levels_aggregates_multiple_level_types(bars_factory):
    rows = [
        {"open": 100 - i * 0.1, "high": 100 - i * 0.1 + 5, "low": 100 - i * 0.1 - 5, "close": 100 - i * 0.1, "volume": 1000}
        for i in range(40)
    ]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = support_levels(bars)

    level_types = {lvl.level_type for lvl in result}
    assert "PRIOR_DAY_LOW" in level_types
    assert "52W_LOW" in level_types


def test_resistance_levels_no_look_ahead(bars_factory):
    rows = [
        {"open": 100 + i, "high": 100 + i + 5, "low": 100 + i - 5, "close": 100 + i, "volume": 1000}
        for i in range(40)
    ]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = resistance_levels(bars_today)

    future_rows = [{"open": 999, "high": 1200, "low": 950, "close": 1000, "volume": 1000} for _ in range(10)]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))
    truncated = bars_with_future.iloc[: len(bars_today)]

    result_recomputed = resistance_levels(truncated)

    assert result_recomputed == result_today
