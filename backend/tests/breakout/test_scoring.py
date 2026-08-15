"""Tests for app.breakout.scoring.

Covers: BREAKOUT_WEIGHTS/DIP_BUY_WEIGHTS spec invariant, score_tier
boundaries, score_breakout/score_dip_buy component weighting and the
market-regime multiplier (including STRONG_BEAR reducing score without
being a hard rejection), the 100 cap, and the standalone
breakout_rejection_reason/dip_buy_rejection_reason checklist functions.

NOTE ON WEIGHT-SUM SPEC: docs/engine.md's prose says "remaining 88 points
renormalized to 100" but its own per-component weight tables for both
setups sum to 99 (breakout: 17+23+11+11+17+11+6+3) and 100 (dip-buy:
15+18+20+18+12+10+5+2) respectively — the doc's prose and its own table
are mutually inconsistent. BREAKOUT_WEIGHTS/DIP_BUY_WEIGHTS in source
match the tables (99 and 100), not the "88" prose. These tests assert
what the code actually implements (matching the tables) rather than the
contradictory "88" prose line — see task summary for the doc
inconsistency writeup.
"""
from __future__ import annotations

import pytest

from app.breakout.scoring import (
    BREAKOUT_WEIGHTS,
    DIP_BUY_WEIGHTS,
    REGIME_SCORE_MULTIPLIER,
    SCORE_TIERS,
    breakout_rejection_reason,
    dip_buy_rejection_reason,
    score_breakout,
    score_dip_buy,
    score_tier,
)
from app.market.market_regime import BEAR, BULL, NEUTRAL, STRONG_BEAR, STRONG_BULL

# --- weight table spec invariants -------------------------------------------


def test_breakout_weights_sum_matches_documented_table():
    # docs/engine.md's per-component breakout table: 17+23+11+11+17+11+6+3
    assert sum(BREAKOUT_WEIGHTS.values()) == pytest.approx(99)


def test_dip_buy_weights_sum_matches_documented_table():
    # docs/engine.md's per-component dip-buy table: 15+18+20+18+12+10+5+2
    assert sum(DIP_BUY_WEIGHTS.values()) == pytest.approx(100)


def test_breakout_weights_has_all_eight_documented_components():
    assert set(BREAKOUT_WEIGHTS.keys()) == {
        "resistance_breakout",
        "relative_volume",
        "candle_quality",
        "trend",
        "retest",
        "relative_strength",
        "market_confirmation",
        "sector_confirmation",
    }


def test_dip_buy_weights_has_all_eight_documented_components():
    assert set(DIP_BUY_WEIGHTS.keys()) == {
        "support_hold_quality",
        "relative_volume",
        "reversal_candle_quality",
        "trend",
        "retest",
        "relative_strength",
        "market_confirmation",
        "sector_confirmation",
    }


def test_dip_buy_weights_reweight_trend_and_reversal_quality_higher_than_breakout():
    # docs/engine.md: "trend strength and reversal-candle quality matter
    # more than breakout volume does for this setup" — sanity check the
    # reweighting direction, not exact numbers.
    assert DIP_BUY_WEIGHTS["trend"] > BREAKOUT_WEIGHTS["trend"]
    assert DIP_BUY_WEIGHTS["reversal_candle_quality"] > BREAKOUT_WEIGHTS["candle_quality"]


def test_regime_score_multiplier_covers_all_five_regimes():
    assert set(REGIME_SCORE_MULTIPLIER.keys()) == {STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR}


def test_regime_score_multiplier_ordering_is_monotonic_bull_to_bear():
    assert (
        REGIME_SCORE_MULTIPLIER[STRONG_BULL]
        > REGIME_SCORE_MULTIPLIER[BULL]
        > REGIME_SCORE_MULTIPLIER[NEUTRAL]
        > REGIME_SCORE_MULTIPLIER[BEAR]
        > REGIME_SCORE_MULTIPLIER[STRONG_BEAR]
    )


# --- score_tier boundaries ---------------------------------------------------


@pytest.mark.parametrize(
    "score,expected_tier",
    [
        (100.0, "A+"),
        (90.0, "A+"),
        (89.999, "A"),
        (80.0, "A"),
        (79.999, "B"),
        (70.0, "B"),
        (69.999, "C"),
        (60.0, "C"),
        (59.999, "IGNORE"),
        (0.0, "IGNORE"),
    ],
)
def test_score_tier_boundaries(score, expected_tier):
    assert score_tier(score) == expected_tier


def test_score_tiers_table_matches_documented_thresholds():
    assert SCORE_TIERS == [(90, "A+"), (80, "A"), (70, "B"), (60, "C")]


# --- score_breakout -----------------------------------------------------


def _max_breakout_kwargs(market_regime: str = BULL) -> dict:
    return dict(
        resistance_breakout_fired=True,
        rvol=10.0,
        candle_body_pct=100.0,
        trend_aligned=True,
        retest_held=True,
        relative_strength_vs_nifty=50.0,
        market_regime=market_regime,
        sector_confirmed=True,
        min_rvol=1.5,
    )


def test_score_breakout_all_zero_when_nothing_fires():
    result = score_breakout(
        resistance_breakout_fired=False,
        rvol=None,
        candle_body_pct=None,
        trend_aligned=False,
        retest_held=False,
        relative_strength_vs_nifty=None,
        market_regime=NEUTRAL,
        sector_confirmed=False,
    )

    assert result.total == pytest.approx(0.0)
    assert result.tier == "IGNORE"
    assert all(v == 0.0 for v in result.breakdown.values())


def test_score_breakout_max_score_in_bull_regime_is_near_full_weight_sum():
    result = score_breakout(**_max_breakout_kwargs(BULL))

    assert result.total == pytest.approx(sum(BREAKOUT_WEIGHTS.values()) * REGIME_SCORE_MULTIPLIER[BULL])
    assert result.tier == "A+"


def test_score_breakout_capped_at_100_in_strong_bull_regime():
    result = score_breakout(**_max_breakout_kwargs(STRONG_BULL))

    assert result.total == pytest.approx(100.0)
    assert result.tier == "A+"


def test_score_breakout_strong_bear_reduces_score_but_is_not_a_hard_rejection():
    """docs/engine.md: "Market regime STRONG_BEAR/BEAR reduces score via a
    configurable multiplier for both setups (not a hard rejection)".
    """
    bull_result = score_breakout(**_max_breakout_kwargs(BULL))
    bear_result = score_breakout(**_max_breakout_kwargs(STRONG_BEAR))

    assert bear_result.total < bull_result.total
    assert bear_result.total > 0.0
    assert bear_result.rejected is False
    assert bear_result.rejection_reason is None
    # explicit expected value: market_confirmation doesn't fire outside
    # BULL/STRONG_BULL, so raw total is the full weight sum minus that
    # component's weight, times the STRONG_BEAR multiplier.
    raw_total_without_market_confirmation = sum(BREAKOUT_WEIGHTS.values()) - BREAKOUT_WEIGHTS["market_confirmation"]
    assert bear_result.total == pytest.approx(raw_total_without_market_confirmation * REGIME_SCORE_MULTIPLIER[STRONG_BEAR])


def test_score_breakout_rvol_quality_scales_partial_credit():
    weak_rvol = score_breakout(**{**_max_breakout_kwargs(NEUTRAL), "rvol": 1.5})
    strong_rvol = score_breakout(**{**_max_breakout_kwargs(NEUTRAL), "rvol": 10.0})

    assert weak_rvol.breakdown["relative_volume"] < strong_rvol.breakdown["relative_volume"]
    assert strong_rvol.breakdown["relative_volume"] == pytest.approx(BREAKOUT_WEIGHTS["relative_volume"])


def test_score_breakout_rvol_below_min_contributes_zero_relative_volume():
    result = score_breakout(**{**_max_breakout_kwargs(NEUTRAL), "rvol": 1.0})

    assert result.breakdown["relative_volume"] == 0.0


def test_score_breakout_market_confirmation_only_for_bull_regimes():
    for regime in (BULL, STRONG_BULL):
        result = score_breakout(**_max_breakout_kwargs(regime))
        assert result.breakdown["market_confirmation"] == pytest.approx(BREAKOUT_WEIGHTS["market_confirmation"])

    for regime in (NEUTRAL, BEAR, STRONG_BEAR):
        result = score_breakout(**_max_breakout_kwargs(regime))
        assert result.breakdown["market_confirmation"] == 0.0


def test_score_breakout_total_never_exceeds_100(monkeypatch=None):
    # extreme over-driven inputs should still be capped
    result = score_breakout(
        resistance_breakout_fired=True,
        rvol=1000.0,
        candle_body_pct=1000.0,
        trend_aligned=True,
        retest_held=True,
        relative_strength_vs_nifty=1000.0,
        market_regime=STRONG_BULL,
        sector_confirmed=True,
    )

    assert result.total <= 100.0


# --- score_dip_buy -------------------------------------------------------


def _max_dip_buy_kwargs(market_regime: str = BULL) -> dict:
    return dict(
        support_hold_quality=1.0,
        rvol=10.0,
        reversal_pattern="MORNING_STAR",
        trend_strength=1.0,
        retest_held=True,
        relative_strength_vs_nifty=50.0,
        market_regime=market_regime,
        sector_confirmed=True,
        min_rvol=1.3,
    )


def test_score_dip_buy_all_zero_when_nothing_fires():
    result = score_dip_buy(
        support_hold_quality=None,
        rvol=None,
        reversal_pattern=None,
        trend_strength=None,
        retest_held=False,
        relative_strength_vs_nifty=None,
        market_regime=NEUTRAL,
        sector_confirmed=False,
    )

    assert result.total == pytest.approx(0.0)
    assert result.tier == "IGNORE"


def test_score_dip_buy_max_score_in_bull_regime():
    result = score_dip_buy(**_max_dip_buy_kwargs(BULL))

    assert result.total == pytest.approx(sum(DIP_BUY_WEIGHTS.values()) * REGIME_SCORE_MULTIPLIER[BULL])
    assert result.tier == "A+"


def test_score_dip_buy_capped_at_100_in_strong_bull_regime():
    result = score_dip_buy(**_max_dip_buy_kwargs(STRONG_BULL))

    assert result.total == pytest.approx(100.0)


def test_score_dip_buy_strong_bear_reduces_score_but_is_not_a_hard_rejection():
    bull_result = score_dip_buy(**_max_dip_buy_kwargs(BULL))
    bear_result = score_dip_buy(**_max_dip_buy_kwargs(STRONG_BEAR))

    assert bear_result.total < bull_result.total
    assert bear_result.total > 0.0
    assert bear_result.rejected is False
    assert bear_result.rejection_reason is None


def test_score_dip_buy_reversal_pattern_quality_ranking():
    morning_star = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "reversal_pattern": "MORNING_STAR"})
    engulfing = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "reversal_pattern": "BULLISH_ENGULFING"})
    hammer = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "reversal_pattern": "HAMMER"})
    unknown = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "reversal_pattern": "SOME_UNKNOWN_PATTERN"})

    assert (
        morning_star.breakdown["reversal_candle_quality"]
        > engulfing.breakdown["reversal_candle_quality"]
        > hammer.breakdown["reversal_candle_quality"]
        > unknown.breakdown["reversal_candle_quality"]
    )
    assert unknown.breakdown["reversal_candle_quality"] == 0.0


def test_score_dip_buy_none_reversal_pattern_contributes_zero():
    result = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "reversal_pattern": None})

    assert result.breakdown["reversal_candle_quality"] == 0.0


def test_score_dip_buy_support_hold_quality_scales_partial_credit():
    weak = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "support_hold_quality": 0.2})
    strong = score_dip_buy(**{**_max_dip_buy_kwargs(NEUTRAL), "support_hold_quality": 1.0})

    assert weak.breakdown["support_hold_quality"] < strong.breakdown["support_hold_quality"]
    assert strong.breakdown["support_hold_quality"] == pytest.approx(DIP_BUY_WEIGHTS["support_hold_quality"])


def test_score_dip_buy_total_never_exceeds_100():
    result = score_dip_buy(
        support_hold_quality=1000.0,
        rvol=1000.0,
        reversal_pattern="MORNING_STAR",
        trend_strength=1000.0,
        retest_held=True,
        relative_strength_vs_nifty=1000.0,
        market_regime=STRONG_BULL,
        sector_confirmed=True,
    )

    assert result.total <= 100.0


# --- breakout_rejection_reason ------------------------------------------


def test_breakout_rejection_reason_no_close_above_resistance():
    reason = breakout_rejection_reason(
        closed_above_resistance=False, rvol=None, min_rvol=1.5, upper_wick_pct=0.0, closed_back_below_level=False
    )

    assert reason == "NOT_CONFIRMED_NO_CLOSE_ABOVE_RESISTANCE"


def test_breakout_rejection_reason_rvol_below_minimum():
    reason = breakout_rejection_reason(
        closed_above_resistance=True, rvol=1.0, min_rvol=1.5, upper_wick_pct=0.0, closed_back_below_level=False
    )

    assert reason == "RVOL_BELOW_MINIMUM"


def test_breakout_rejection_reason_rvol_none_treated_as_below_minimum():
    reason = breakout_rejection_reason(
        closed_above_resistance=True, rvol=None, min_rvol=1.5, upper_wick_pct=0.0, closed_back_below_level=False
    )

    assert reason == "RVOL_BELOW_MINIMUM"


def test_breakout_rejection_reason_severe_wick():
    reason = breakout_rejection_reason(
        closed_above_resistance=True, rvol=2.0, min_rvol=1.5, upper_wick_pct=70.0, closed_back_below_level=False
    )

    assert reason == "SEVERE_REJECTION_WICK"


def test_breakout_rejection_reason_wick_boundary_60_not_rejected():
    reason = breakout_rejection_reason(
        closed_above_resistance=True, rvol=2.0, min_rvol=1.5, upper_wick_pct=60.0, closed_back_below_level=False
    )

    assert reason is None


def test_breakout_rejection_reason_closed_back_below_level():
    reason = breakout_rejection_reason(
        closed_above_resistance=True, rvol=2.0, min_rvol=1.5, upper_wick_pct=10.0, closed_back_below_level=True
    )

    assert reason == "INVALIDATED_CLOSE_BELOW_BREAKOUT_LEVEL"


def test_breakout_rejection_reason_none_when_all_clear():
    reason = breakout_rejection_reason(
        closed_above_resistance=True, rvol=2.0, min_rvol=1.5, upper_wick_pct=10.0, closed_back_below_level=False
    )

    assert reason is None


def test_breakout_rejection_reason_precedence_order():
    # multiple rejection conditions true at once -> earliest check wins
    reason = breakout_rejection_reason(
        closed_above_resistance=False, rvol=None, min_rvol=1.5, upper_wick_pct=90.0, closed_back_below_level=True
    )

    assert reason == "NOT_CONFIRMED_NO_CLOSE_ABOVE_RESISTANCE"


# --- dip_buy_rejection_reason --------------------------------------------


def test_dip_buy_rejection_reason_precondition_not_met():
    reason = dip_buy_rejection_reason(
        uptrend_precondition_met=False,
        closed_below_support=False,
        reversal_rvol=None,
        min_rvol=1.3,
        made_new_low_after_confirmed=False,
    )

    assert reason == "UPTREND_PRECONDITION_NOT_MET"


def test_dip_buy_rejection_reason_closed_below_support():
    reason = dip_buy_rejection_reason(
        uptrend_precondition_met=True,
        closed_below_support=True,
        reversal_rvol=None,
        min_rvol=1.3,
        made_new_low_after_confirmed=False,
    )

    assert reason == "NOT_CONFIRMED_CLOSED_BELOW_SUPPORT"


def test_dip_buy_rejection_reason_reversal_rvol_below_minimum():
    reason = dip_buy_rejection_reason(
        uptrend_precondition_met=True,
        closed_below_support=False,
        reversal_rvol=1.0,
        min_rvol=1.3,
        made_new_low_after_confirmed=False,
    )

    assert reason == "REVERSAL_RVOL_BELOW_MINIMUM"


def test_dip_buy_rejection_reason_new_low_after_confirmed():
    reason = dip_buy_rejection_reason(
        uptrend_precondition_met=True,
        closed_below_support=False,
        reversal_rvol=2.0,
        min_rvol=1.3,
        made_new_low_after_confirmed=True,
    )

    assert reason == "INVALIDATED_NEW_LOW_BELOW_DIP"


def test_dip_buy_rejection_reason_none_when_all_clear():
    reason = dip_buy_rejection_reason(
        uptrend_precondition_met=True,
        closed_below_support=False,
        reversal_rvol=2.0,
        min_rvol=1.3,
        made_new_low_after_confirmed=False,
    )

    assert reason is None


def test_dip_buy_rejection_reason_precedence_order():
    reason = dip_buy_rejection_reason(
        uptrend_precondition_met=False,
        closed_below_support=True,
        reversal_rvol=None,
        min_rvol=1.3,
        made_new_low_after_confirmed=True,
    )

    assert reason == "UPTREND_PRECONDITION_NOT_MET"
