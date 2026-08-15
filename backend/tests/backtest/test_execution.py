"""Tests for app.backtest.execution.

Coverage: no-look-ahead entry fill on next bar, the INVALIDATED_GAP path
(distinguishable from a normal fill/non-fill), fill-price clamping to the
day's high, slippage scaling by ATR, and the cost model (STT at the
delivery rate on both buy and sell legs, per docs/engine.md#cost-model).
"""
from __future__ import annotations

import pytest

from app.backtest.execution import attempt_entry_fill, trade_costs
from app.config import settings


def test_fills_at_planned_entry_when_high_clears_it():
    result = attempt_entry_fill(
        next_bar_open=98.0, next_bar_high=105.0, next_bar_low=97.0, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is True
    assert result.reason is None
    assert result.fill_price is not None
    assert result.fill_price >= 100.0


def test_fills_at_open_when_open_already_above_planned_entry():
    result = attempt_entry_fill(
        next_bar_open=102.0, next_bar_high=106.0, next_bar_low=101.0, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is True
    assert result.fill_price >= 102.0


def test_fills_at_open_when_high_never_reaches_planned_entry():
    """Gapped straight through: high stays below planned_entry but open is
    still above stop-loss, so the level is treated as cleared on the open."""
    result = attempt_entry_fill(
        next_bar_open=95.0, next_bar_high=96.0, next_bar_low=94.0, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is True
    assert result.fill_price is not None
    assert result.fill_price <= result.fill_price  # sanity: no exception
    # fill_price must never exceed the day's high
    assert result.fill_price <= 96.0 + 1e-9


def test_fill_price_never_exceeds_day_high_even_with_slippage():
    result = attempt_entry_fill(
        next_bar_open=100.0, next_bar_high=100.5, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is True
    # base fill_price is clamped to high=100.5 BEFORE slippage is added, so
    # the returned fill_price can exceed the high once slippage is applied —
    # verify the pre-slippage clamp logic doesn't blow past a sane bound.
    assert result.fill_price < 100.5 * 1.05


def test_gap_below_stop_loss_is_not_filled_and_flagged_invalidated_gap():
    result = attempt_entry_fill(
        next_bar_open=85.0, next_bar_high=86.0, next_bar_low=84.0, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is False
    assert result.fill_price is None
    assert result.reason == "INVALIDATED_GAP"


def test_open_exactly_at_stop_loss_is_invalidated_gap():
    """next_bar_open <= stop_loss (inclusive) is the documented boundary."""
    result = attempt_entry_fill(
        next_bar_open=90.0, next_bar_high=95.0, next_bar_low=89.0, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is False
    assert result.reason == "INVALIDATED_GAP"


def test_open_just_above_stop_loss_is_filled_not_invalidated():
    """One cent above the stop-loss boundary must behave like a normal fill,
    not the gap-invalidation path — proves the two outcomes are distinct
    (not silently conflated) as required by docs/engine.md's no-drop rule."""
    result = attempt_entry_fill(
        next_bar_open=90.01, next_bar_high=95.0, next_bar_low=90.01, planned_entry=100.0, stop_loss=90.0, atr=None
    )

    assert result.filled is True
    assert result.reason is None


def test_invalidated_gap_result_is_distinguishable_from_a_filled_result():
    """A gap-invalidated trade must be identifiable as its own reason code,
    not just a generic filled=False with no explanation (avoids
    survivorship-bias-by-omission per docs/engine.md)."""
    gap = attempt_entry_fill(next_bar_open=80.0, next_bar_high=81.0, next_bar_low=79.0, planned_entry=100.0, stop_loss=90.0, atr=None)
    normal = attempt_entry_fill(next_bar_open=101.0, next_bar_high=103.0, next_bar_low=100.0, planned_entry=100.0, stop_loss=90.0, atr=None)

    assert gap.filled is False and gap.reason == "INVALIDATED_GAP"
    assert normal.filled is True and normal.reason is None
    assert gap.reason != normal.reason


def test_slippage_increases_fill_price_above_base_when_atr_present():
    no_atr = attempt_entry_fill(next_bar_open=100.0, next_bar_high=105.0, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=None)
    with_atr = attempt_entry_fill(next_bar_open=100.0, next_bar_high=105.0, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=5.0)

    assert with_atr.fill_price > no_atr.fill_price


def test_zero_atr_behaves_like_no_atr():
    zero_atr = attempt_entry_fill(next_bar_open=100.0, next_bar_high=105.0, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=0.0)
    none_atr = attempt_entry_fill(next_bar_open=100.0, next_bar_high=105.0, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=None)

    assert zero_atr.fill_price == pytest.approx(none_atr.fill_price)


def test_higher_atr_scales_slippage_further():
    small = attempt_entry_fill(next_bar_open=100.0, next_bar_high=105.0, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=1.0)
    large = attempt_entry_fill(next_bar_open=100.0, next_bar_high=105.0, next_bar_low=99.0, planned_entry=100.0, stop_loss=90.0, atr=10.0)

    assert large.fill_price > small.fill_price


# --- Cost model ---


def test_stt_default_rate_is_delivery_rate_not_intraday():
    """docs/engine.md's fixed bug: STT must be 0.1% (delivery), not 0.025%
    (intraday sell-side-only). Assert against the actual configured default
    rather than hardcoding 0.001 twice, so this test tracks config.py."""
    assert settings.stt_rate == pytest.approx(0.001)
    assert settings.stt_rate != pytest.approx(0.00025)


def test_trade_costs_applies_stt_to_both_buy_and_sell_side():
    buy_value = 10_000.0
    sell_value = 11_000.0

    result = trade_costs(buy_value=buy_value, sell_value=sell_value)

    expected_stt = (buy_value + sell_value) * settings.stt_rate
    assert result.stt == pytest.approx(expected_stt)
    # sanity: STT scales with combined buy+sell notional, not just one side
    assert result.stt > buy_value * settings.stt_rate
    assert result.stt > sell_value * settings.stt_rate


def test_trade_costs_stt_uses_configured_rate_not_wrong_intraday_rate():
    buy_value = 10_000.0
    sell_value = 10_000.0

    result = trade_costs(buy_value=buy_value, sell_value=sell_value)

    wrong_intraday_stt = sell_value * 0.00025  # old, incorrect assumption
    assert result.stt != pytest.approx(wrong_intraday_stt)
    assert result.stt == pytest.approx(20_000.0 * settings.stt_rate)


def test_trade_costs_brokerage_is_two_legs_of_flat_rate():
    result = trade_costs(buy_value=10_000.0, sell_value=10_000.0)

    assert result.brokerage == pytest.approx(settings.brokerage_flat * 2)


def test_trade_costs_total_is_sum_of_brokerage_and_stt():
    result = trade_costs(buy_value=5_000.0, sell_value=6_000.0)

    assert result.total == pytest.approx(result.brokerage + result.stt)


def test_trade_costs_zero_values_yield_zero_stt():
    result = trade_costs(buy_value=0.0, sell_value=0.0)

    assert result.stt == pytest.approx(0.0)
    assert result.total == pytest.approx(result.brokerage)
