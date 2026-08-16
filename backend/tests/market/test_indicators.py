"""Tests for app.market.indicators.

Coverage: happy path column/shape correctness, NaN-until-window-full
behavior (documented as correct, not a bug), empty-input handling, and the
no-look-ahead invariant — appending future bars must never change
already-computed values at earlier indices.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.market.indicators import INDICATOR_COLUMNS, compute_indicators, latest_indicator_row


def test_compute_indicators_empty_returns_empty_frame_with_columns():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = compute_indicators(bars)

    assert result.empty
    assert list(result.columns) == INDICATOR_COLUMNS


def test_compute_indicators_returns_one_row_per_input_row(flat_bars_factory):
    bars = flat_bars_factory(30, price=100.0)

    result = compute_indicators(bars)

    assert len(result) == len(bars)
    assert list(result.index) == list(bars.index)
    assert list(result.columns) == INDICATOR_COLUMNS


def test_ema9_is_not_nan_once_span_reached(flat_bars_factory):
    bars = flat_bars_factory(9, price=100.0)

    result = compute_indicators(bars)

    assert not pd.isna(result["ema9"].iloc[-1])


def test_ema9_is_nan_before_span_reached(flat_bars_factory):
    bars = flat_bars_factory(8, price=100.0)

    result = compute_indicators(bars)

    assert result["ema9"].isna().all()


def test_flat_price_series_ema_equals_price(flat_bars_factory):
    bars = flat_bars_factory(60, price=100.0)

    result = compute_indicators(bars)

    assert result["ema9"].iloc[-1] == pytest.approx(100.0)
    assert result["ema20"].iloc[-1] == pytest.approx(100.0)
    assert result["ema50"].iloc[-1] == pytest.approx(100.0)


def test_sma100_nan_below_100_bars(flat_bars_factory):
    bars = flat_bars_factory(99, price=50.0)

    result = compute_indicators(bars)

    assert result["sma100"].isna().all()


def test_sma100_populated_at_exactly_100_bars(flat_bars_factory):
    bars = flat_bars_factory(100, price=50.0)

    result = compute_indicators(bars)

    assert not pd.isna(result["sma100"].iloc[-1])
    assert result["sma100"].iloc[-1] == pytest.approx(50.0)
    # rows before the window is full remain NaN
    assert result["sma100"].iloc[:99].isna().all()


def test_sma200_nan_below_200_bars(flat_bars_factory):
    bars = flat_bars_factory(150, price=50.0)

    result = compute_indicators(bars)

    assert result["sma200"].isna().all()


def test_rsi_nan_before_period_reached(flat_bars_factory):
    bars = flat_bars_factory(13, price=100.0)

    result = compute_indicators(bars)

    assert result["rsi"].isna().all()


def test_rsi_flat_price_series_is_neutral_when_no_moves(flat_bars_factory):
    """Flat closes => delta always 0 => avg_loss is 0 => RS is inf/NaN
    (division by zero guarded via replace(0, NA)). Document behavior rather
    than assume a specific number, since a divide-by-zero edge case is
    exactly what we want a test to pin down.
    """
    bars = flat_bars_factory(30, price=100.0)

    result = compute_indicators(bars)

    # avg_gain == avg_loss == 0 -> rs = 0/NA -> NaN, so rsi should be NaN
    # rather than raising or silently producing a wrong finite number.
    assert pd.isna(result["rsi"].iloc[-1])


def test_rsi_all_up_moves_saturates_at_100(bars_factory):
    """With every bar an up-move, avg_loss is exactly 0 and avg_gain > 0 —
    RSI is 100 by convention (a pure uptrend is the maximally overbought
    reading), not undefined. Previously this produced NaN (division by a
    zero-turned-NA avg_loss went uncaught) — fixed since a NaN here
    silently dropped a legitimate "very strong" signal from scoring."""
    rows = [{"open": 100 + i, "high": 100 + i, "low": 100 + i, "close": 100 + i, "volume": 1000} for i in range(30)]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = compute_indicators(bars)

    assert result["rsi"].iloc[-1] == 100.0


def test_rsi_bounded_between_0_and_100(bars_factory):
    prices = [100, 102, 99, 105, 101, 98, 110, 108, 95, 120, 90, 130, 85, 140, 80, 150, 75, 160, 70, 170, 65, 180, 60, 190, 55, 200, 50, 210, 45, 220]
    rows = [{"open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 1000} for p in prices]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = compute_indicators(bars)

    valid = result["rsi"].dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_macd_and_signal_populated_once_slow_ema_ready(flat_bars_factory):
    bars = flat_bars_factory(26, price=100.0)

    result = compute_indicators(bars)

    assert not pd.isna(result["macd"].iloc[-1])


def test_macd_flat_price_is_zero(flat_bars_factory):
    bars = flat_bars_factory(60, price=100.0)

    result = compute_indicators(bars)

    assert result["macd"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_atr_nan_before_period_reached(flat_bars_factory):
    bars = flat_bars_factory(13, price=100.0)

    result = compute_indicators(bars)

    assert result["atr"].isna().all()


def test_atr_zero_when_no_range_and_no_gaps(flat_bars_factory):
    bars = flat_bars_factory(20, price=100.0)

    result = compute_indicators(bars)

    assert result["atr"].iloc[-1] == pytest.approx(0.0)


def test_atr_positive_when_true_range_present(bars_factory):
    rows = [
        {"open": 100, "high": 105, "low": 98, "close": 102, "volume": 1000}
        for _ in range(20)
    ]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = compute_indicators(bars)

    assert result["atr"].iloc[-1] > 0


def test_bollinger_bands_nan_before_period_reached(flat_bars_factory):
    bars = flat_bars_factory(19, price=100.0)

    result = compute_indicators(bars)

    assert result["bb_upper"].isna().all()
    assert result["bb_lower"].isna().all()


def test_bollinger_bands_collapse_to_price_when_flat(flat_bars_factory):
    bars = flat_bars_factory(25, price=100.0)

    result = compute_indicators(bars)

    assert result["bb_upper"].iloc[-1] == pytest.approx(100.0)
    assert result["bb_lower"].iloc[-1] == pytest.approx(100.0)


def test_bollinger_upper_always_gte_lower_when_populated(bars_factory):
    prices = [100, 103, 98, 107, 95, 110, 92, 115, 90, 120, 88, 125, 85, 130, 82, 135, 80, 140, 78, 145, 75, 150]
    rows = [{"open": p, "high": p + 2, "low": p - 2, "close": p, "volume": 1000} for p in prices]
    bars = bars_factory(rows, start=date(2024, 1, 1))

    result = compute_indicators(bars)

    valid = result.dropna(subset=["bb_upper", "bb_lower"])
    assert (valid["bb_upper"] >= valid["bb_lower"]).all()


def test_latest_indicator_row_empty_bars_returns_all_none():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    result = latest_indicator_row(bars)

    assert set(result.keys()) == set(INDICATOR_COLUMNS)
    assert all(v is None for v in result.values())


def test_latest_indicator_row_returns_floats_not_numpy_types(flat_bars_factory):
    bars = flat_bars_factory(60, price=100.0)

    result = latest_indicator_row(bars)

    assert isinstance(result["ema9"], float)
    assert result["sma200"] is None  # not enough bars for 200-window SMA


def test_latest_indicator_row_matches_last_row_of_compute_indicators(flat_bars_factory):
    bars = flat_bars_factory(60, price=123.45)

    full = compute_indicators(bars)
    latest = latest_indicator_row(bars)

    for col in INDICATOR_COLUMNS:
        expected = full[col].iloc[-1]
        if pd.isna(expected):
            assert latest[col] is None
        else:
            assert latest[col] == pytest.approx(float(expected))


# --- No-look-ahead invariant ---------------------------------------------


def test_no_look_ahead_appending_future_bars_does_not_change_past_output(bars_factory):
    """Core engine.md invariant: compute_indicators must never let a future
    bar change an already-computed value at an earlier index. Compute once
    on a truncated history, then again after appending synthetic future
    bars, and assert the overlapping prefix is identical.
    """
    rng = np.random.default_rng(seed=42)
    n_initial = 80
    prices = 100 + np.cumsum(rng.normal(0, 1, n_initial))
    rows = [
        {"open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 1000}
        for p in prices
    ]
    bars_today = bars_factory(rows, start=date(2024, 1, 1))

    result_today = compute_indicators(bars_today)

    future_prices = 100 + np.cumsum(rng.normal(0, 1, 15))
    future_rows = [
        {"open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 1000}
        for p in future_prices
    ]
    bars_with_future = bars_factory(rows + future_rows, start=date(2024, 1, 1))

    result_with_future = compute_indicators(bars_with_future)

    prefix = result_with_future.iloc[: len(result_today)]
    pd.testing.assert_frame_equal(prefix, result_today, check_exact=False)


def test_no_look_ahead_latest_indicator_row_reflects_truncation_point(flat_bars_factory, bars_factory):
    """latest_indicator_row on a truncated history must match what
    compute_indicators produced for that same truncation point when future
    bars are later appended — i.e. "today"'s snapshot is stable.
    """
    bars_today = flat_bars_factory(60, price=200.0)
    snapshot_today = latest_indicator_row(bars_today)

    future_rows = [{"open": 999, "high": 1050, "low": 950, "close": 1000, "volume": 5000} for _ in range(5)]
    extended = bars_factory(
        [
            {"open": 200.0, "high": 200.0, "low": 200.0, "close": 200.0, "volume": 100_000}
            for _ in range(60)
        ]
        + future_rows,
        start=date(2024, 1, 1),
    )

    recomputed_full = compute_indicators(extended)
    recomputed_at_today = recomputed_full.iloc[59]

    for col in INDICATOR_COLUMNS:
        snap_val = snapshot_today[col]
        recomputed_val = recomputed_at_today[col]
        if snap_val is None:
            assert pd.isna(recomputed_val)
        else:
            assert recomputed_val == pytest.approx(snap_val)
