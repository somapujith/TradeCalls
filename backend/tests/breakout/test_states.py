"""Tests for app.breakout.states — BreakoutState/DipBuyState enums,
TERMINAL_STATES, is_terminal().
"""
from __future__ import annotations

import pytest

from app.breakout.states import BreakoutState, DipBuyState, TERMINAL_STATES, is_terminal


# --- BreakoutState -----------------------------------------------------------


def test_breakout_state_is_str_enum():
    assert BreakoutState.WATCH == "WATCH"
    assert isinstance(BreakoutState.WATCH, str)
    assert isinstance(BreakoutState.WATCH, BreakoutState)


@pytest.mark.parametrize(
    "member,value",
    [
        (BreakoutState.WATCH, "WATCH"),
        (BreakoutState.APPROACHING, "APPROACHING"),
        (BreakoutState.BREAKOUT_ATTEMPT, "BREAKOUT_ATTEMPT"),
        (BreakoutState.CANDLE_CONFIRMATION, "CANDLE_CONFIRMATION"),
        (BreakoutState.VOLUME_CONFIRMATION, "VOLUME_CONFIRMATION"),
        (BreakoutState.CONFIRMED, "CONFIRMED"),
        (BreakoutState.RETEST_PENDING, "RETEST_PENDING"),
        (BreakoutState.RETEST_CONFIRMED, "RETEST_CONFIRMED"),
        (BreakoutState.TRADE_ACTIVE, "TRADE_ACTIVE"),
        (BreakoutState.TARGET_HIT, "TARGET_HIT"),
        (BreakoutState.INVALIDATED, "INVALIDATED"),
        (BreakoutState.SESSION_END, "SESSION_END"),
        (BreakoutState.NOT_CONFIRMED, "NOT_CONFIRMED"),
    ],
)
def test_breakout_state_all_members_present(member, value):
    assert member.value == value


def test_breakout_state_has_exactly_13_members():
    assert len(BreakoutState) == 13


# --- DipBuyState ---------------------------------------------------------


def test_dip_buy_state_is_str_enum():
    assert DipBuyState.UPTREND_CONFIRMED == "UPTREND_CONFIRMED"
    assert isinstance(DipBuyState.UPTREND_CONFIRMED, str)
    assert isinstance(DipBuyState.UPTREND_CONFIRMED, DipBuyState)


@pytest.mark.parametrize(
    "member,value",
    [
        (DipBuyState.UPTREND_CONFIRMED, "UPTREND_CONFIRMED"),
        (DipBuyState.PULLBACK_IN_PROGRESS, "PULLBACK_IN_PROGRESS"),
        (DipBuyState.SUPPORT_TEST, "SUPPORT_TEST"),
        (DipBuyState.REVERSAL_CANDLE, "REVERSAL_CANDLE"),
        (DipBuyState.VOLUME_CONFIRMATION, "VOLUME_CONFIRMATION"),
        (DipBuyState.CONFIRMED, "CONFIRMED"),
        (DipBuyState.RETEST_PENDING, "RETEST_PENDING"),
        (DipBuyState.RETEST_CONFIRMED, "RETEST_CONFIRMED"),
        (DipBuyState.TRADE_ACTIVE, "TRADE_ACTIVE"),
        (DipBuyState.TARGET_HIT, "TARGET_HIT"),
        (DipBuyState.INVALIDATED, "INVALIDATED"),
        (DipBuyState.SESSION_END, "SESSION_END"),
        (DipBuyState.NOT_CONFIRMED, "NOT_CONFIRMED"),
    ],
)
def test_dip_buy_state_all_members_present(member, value):
    assert member.value == value


def test_dip_buy_state_has_exactly_13_members():
    assert len(DipBuyState) == 13


# --- TERMINAL_STATES / is_terminal -------------------------------------------


def test_terminal_states_contains_expected_four():
    assert TERMINAL_STATES == {"TARGET_HIT", "INVALIDATED", "SESSION_END", "NOT_CONFIRMED"}


@pytest.mark.parametrize("state", ["TARGET_HIT", "INVALIDATED", "SESSION_END", "NOT_CONFIRMED"])
def test_is_terminal_true_for_terminal_states(state):
    assert is_terminal(state) is True


@pytest.mark.parametrize(
    "state",
    [
        "WATCH",
        "APPROACHING",
        "BREAKOUT_ATTEMPT",
        "CANDLE_CONFIRMATION",
        "VOLUME_CONFIRMATION",
        "CONFIRMED",
        "RETEST_PENDING",
        "RETEST_CONFIRMED",
        "TRADE_ACTIVE",
        "UPTREND_CONFIRMED",
        "PULLBACK_IN_PROGRESS",
        "SUPPORT_TEST",
        "REVERSAL_CANDLE",
    ],
)
def test_is_terminal_false_for_non_terminal_states(state):
    assert is_terminal(state) is False


def test_is_terminal_false_for_unknown_string():
    assert is_terminal("SOME_MADE_UP_STATE") is False


def test_is_terminal_accepts_enum_members_directly():
    # BreakoutState/DipBuyState are str subclasses, so enum members should
    # compare equal to their string value inside the TERMINAL_STATES set.
    assert is_terminal(BreakoutState.TARGET_HIT) is True
    assert is_terminal(BreakoutState.NOT_CONFIRMED) is True
    assert is_terminal(BreakoutState.WATCH) is False
    assert is_terminal(DipBuyState.INVALIDATED) is True
    assert is_terminal(DipBuyState.UPTREND_CONFIRMED) is False


def test_not_confirmed_is_distinct_state_in_both_enums():
    # NOT_CONFIRMED is documented as a hard-rejection sink distinct from
    # INVALIDATED (never reached CONFIRMED) — verify both enums define it
    # as a separate member, not an alias.
    assert BreakoutState.NOT_CONFIRMED != BreakoutState.INVALIDATED
    assert DipBuyState.NOT_CONFIRMED != DipBuyState.INVALIDATED
