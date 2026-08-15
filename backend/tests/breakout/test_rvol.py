"""Tests for app.breakout.rvol.relative_volume."""
from __future__ import annotations

import pandas as pd
import pytest

from app.breakout.rvol import relative_volume


def test_rvol_none_when_insufficient_history():
    # lookback=20 requires 21 data points (20 baseline + today); 20 total
    # is one short.
    series = pd.Series([100_000] * 20)

    assert relative_volume(series, lookback=20) is None


def test_rvol_none_when_history_exactly_lookback_length():
    series = pd.Series([100_000] * 20, dtype=float)

    assert relative_volume(series, lookback=20) is None


def test_rvol_computed_when_history_exactly_lookback_plus_one():
    series = pd.Series([100_000] * 21, dtype=float)

    result = relative_volume(series, lookback=20)

    assert result == pytest.approx(1.0)


def test_rvol_equals_one_for_flat_volume():
    series = pd.Series([50_000] * 25, dtype=float)

    result = relative_volume(series, lookback=20)

    assert result == pytest.approx(1.0)


def test_rvol_doubled_when_today_is_double_baseline():
    series = pd.Series([100_000] * 20 + [200_000], dtype=float)

    result = relative_volume(series, lookback=20)

    assert result == pytest.approx(2.0)


def test_rvol_excludes_today_from_baseline_average():
    # baseline should be the mean of the 20 bars BEFORE today, not including
    # today's own (huge) volume.
    series = pd.Series([100_000] * 20 + [10_000_000], dtype=float)

    result = relative_volume(series, lookback=20)

    assert result == pytest.approx(10_000_000 / 100_000)


def test_rvol_none_when_baseline_is_zero():
    series = pd.Series([0] * 20 + [100_000], dtype=float)

    assert relative_volume(series, lookback=20) is None


def test_rvol_none_when_baseline_is_negative():
    # Defensive: volume should never be negative, but the function guards
    # baseline <= 0 explicitly.
    series = pd.Series([-100] * 20 + [100_000], dtype=float)

    assert relative_volume(series, lookback=20) is None


def test_rvol_default_lookback_is_20():
    series_20 = pd.Series([100_000] * 21, dtype=float)

    assert relative_volume(series_20) == pytest.approx(1.0)


def test_rvol_custom_lookback_period():
    series = pd.Series([100_000] * 5 + [500_000], dtype=float)

    result = relative_volume(series, lookback=5)

    assert result == pytest.approx(5.0)


def test_rvol_empty_series_returns_none():
    series = pd.Series([], dtype=float)

    assert relative_volume(series, lookback=20) is None


def test_rvol_uses_only_trailing_window_not_full_history():
    # 30 bars of low volume, then a spike baseline period, then today.
    # lookback=5 should only average the 5 bars immediately before today,
    # not the whole 30-bar tail.
    series = pd.Series(
        [1_000_000] * 25 + [100_000] * 5 + [200_000], dtype=float
    )

    result = relative_volume(series, lookback=5)

    assert result == pytest.approx(2.0)


def test_rvol_no_look_ahead_unchanged_when_future_bars_appended():
    series_today = pd.Series([100_000] * 20 + [150_000], dtype=float)
    result_today = relative_volume(series_today, lookback=20)

    # Caller truncates volume_history to "today" — simulate appending
    # future days and re-slicing back to the same "today" cutoff.
    series_with_future = pd.concat(
        [series_today, pd.Series([999_999, 999_999, 999_999], dtype=float)],
        ignore_index=True,
    )
    result_recomputed = relative_volume(series_with_future.iloc[: len(series_today)], lookback=20)

    assert result_recomputed == result_today
