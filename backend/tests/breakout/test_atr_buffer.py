"""Tests for app.breakout.atr_buffer.atr_entry_buffer."""
from __future__ import annotations

import pytest

from app.breakout.atr_buffer import atr_entry_buffer


def test_atr_buffer_adds_atr_scaled_amount_with_default_multiple():
    result = atr_entry_buffer(level=100.0, atr=2.0)

    assert result == pytest.approx(100.0 + 2.0 * 0.1)


def test_atr_buffer_respects_custom_buffer_atr_multiple():
    result = atr_entry_buffer(level=100.0, atr=2.0, buffer_atr_multiple=0.05)

    assert result == pytest.approx(100.0 + 2.0 * 0.05)


def test_atr_buffer_falls_back_to_flat_pct_when_atr_is_none():
    result = atr_entry_buffer(level=100.0, atr=None)

    assert result == pytest.approx(100.0 * 1.001)


def test_atr_buffer_falls_back_to_flat_pct_when_atr_is_zero():
    result = atr_entry_buffer(level=100.0, atr=0.0)

    assert result == pytest.approx(100.0 * 1.001)


def test_atr_buffer_falls_back_to_flat_pct_when_atr_is_negative():
    # Defensive: ATR should never be negative in practice, but the function
    # explicitly guards against it (atr <= 0), not just atr is None.
    result = atr_entry_buffer(level=100.0, atr=-5.0)

    assert result == pytest.approx(100.0 * 1.001)


def test_atr_buffer_entry_is_always_above_level_when_atr_present():
    result = atr_entry_buffer(level=50.0, atr=1.5)

    assert result > 50.0


def test_atr_buffer_entry_is_always_above_level_when_atr_missing():
    result = atr_entry_buffer(level=50.0, atr=None)

    assert result > 50.0


def test_atr_buffer_scales_linearly_with_atr():
    small_atr = atr_entry_buffer(level=100.0, atr=1.0, buffer_atr_multiple=0.1)
    large_atr = atr_entry_buffer(level=100.0, atr=10.0, buffer_atr_multiple=0.1)

    assert large_atr - 100.0 == pytest.approx((small_atr - 100.0) * 10)


def test_atr_buffer_handles_zero_level_with_atr_present():
    result = atr_entry_buffer(level=0.0, atr=1.0)

    assert result == pytest.approx(0.1)


def test_atr_buffer_handles_zero_level_without_atr():
    # level * 1.001 == 0 when level is 0 — edge case, should not raise.
    result = atr_entry_buffer(level=0.0, atr=None)

    assert result == pytest.approx(0.0)


@pytest.mark.parametrize("level", [1.0, 100.0, 10_000.0, 0.5])
def test_atr_buffer_various_boundary_levels_no_atr(level):
    result = atr_entry_buffer(level=level, atr=None)

    assert result == pytest.approx(level * 1.001)
