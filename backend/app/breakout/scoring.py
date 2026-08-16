"""Breakout + dip-buy scores, hard rejection rules for both — docs/engine.md#scoring.

VWAP (10) and news catalyst (2) excluded in v1 — remaining 88 points
renormalized to 100 for both setups, reweighted per setup type.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.market.market_regime import BEAR, BULL, NEUTRAL, STRONG_BEAR, STRONG_BULL

# market_regime.py exposes plain string constants (STRONG_BULL..STRONG_BEAR),
# not an enum — this multiplier table lives here since scoring is the only
# consumer that needs a regime -> score-adjustment mapping (docs/engine.md's
# "Hard rejection rules" section: regime reduces score, doesn't hard-reject).
REGIME_SCORE_MULTIPLIER: dict[str, float] = {
    STRONG_BULL: 1.10,
    BULL: 1.0,
    NEUTRAL: 0.90,
    BEAR: 0.75,
    STRONG_BEAR: 0.55,
}

BREAKOUT_WEIGHTS: dict[str, float] = {
    "resistance_breakout": 17,
    "relative_volume": 23,
    "candle_quality": 11,
    "trend": 11,
    "retest": 17,
    "relative_strength": 11,
    "market_confirmation": 6,
    "sector_confirmation": 3,
}

DIP_BUY_WEIGHTS: dict[str, float] = {
    "support_hold_quality": 15,
    "relative_volume": 18,
    "reversal_candle_quality": 20,
    "trend": 18,
    "retest": 12,
    "relative_strength": 10,
    "market_confirmation": 5,
    "sector_confirmation": 2,
}

SCORE_TIERS = [
    (90, "A+"),
    (80, "A"),
    (70, "B"),
    (60, "C"),
]


@dataclass(frozen=True)
class ScoreResult:
    total: float
    tier: str
    breakdown: dict[str, float] = field(default_factory=dict)
    rejected: bool = False
    rejection_reason: str | None = None


def score_tier(total: float) -> str:
    for threshold, tier in SCORE_TIERS:
        if total >= threshold:
            return tier
    return "IGNORE"


def _weighted_component(fired: bool, weight: float, quality: float = 1.0) -> float:
    """quality in [0, 1] lets a component contribute partial credit
    (e.g. RVOL of 1.6 vs the 1.5 minimum isn't as strong as RVOL of 4.0)."""
    if not fired:
        return 0.0
    return weight * max(0.0, min(1.0, quality))


def score_breakout(
    *,
    resistance_breakout_fired: bool,
    rvol: float | None,
    candle_body_pct: float | None,
    trend_aligned: bool,
    retest_held: bool,
    relative_strength_vs_nifty: float | None,
    market_regime: str,
    sector_confirmed: bool,
    min_rvol: float = 1.2,  # was 1.5 — synced with breakout_engine.MIN_RVOL_CONFIRM, see that file
) -> ScoreResult:
    breakdown: dict[str, float] = {}

    breakdown["resistance_breakout"] = _weighted_component(resistance_breakout_fired, BREAKOUT_WEIGHTS["resistance_breakout"])

    rvol_quality = min((rvol or 0) / (min_rvol * 2), 1.0) if rvol else 0.0
    breakdown["relative_volume"] = _weighted_component(bool(rvol and rvol >= min_rvol), BREAKOUT_WEIGHTS["relative_volume"], rvol_quality)

    candle_quality_ratio = min((candle_body_pct or 0) / 70.0, 1.0)
    breakdown["candle_quality"] = _weighted_component(bool(candle_body_pct and candle_body_pct >= 40), BREAKOUT_WEIGHTS["candle_quality"], candle_quality_ratio)

    breakdown["trend"] = _weighted_component(trend_aligned, BREAKOUT_WEIGHTS["trend"])
    breakdown["retest"] = _weighted_component(retest_held, BREAKOUT_WEIGHTS["retest"])

    rs_quality = min(max((relative_strength_vs_nifty or 0) / 10.0, 0), 1.0)
    breakdown["relative_strength"] = _weighted_component(bool(relative_strength_vs_nifty and relative_strength_vs_nifty > 0), BREAKOUT_WEIGHTS["relative_strength"], rs_quality)

    breakdown["market_confirmation"] = _weighted_component(market_regime in (BULL, STRONG_BULL), BREAKOUT_WEIGHTS["market_confirmation"])
    breakdown["sector_confirmation"] = _weighted_component(sector_confirmed, BREAKOUT_WEIGHTS["sector_confirmation"])

    raw_total = sum(breakdown.values())
    regime_adjusted = raw_total * REGIME_SCORE_MULTIPLIER.get(market_regime, 1.0)
    total = min(regime_adjusted, 100.0)

    return ScoreResult(total=total, tier=score_tier(total), breakdown=breakdown)


def score_dip_buy(
    *,
    support_hold_quality: float | None,  # 0-1, how cleanly support was respected
    rvol: float | None,
    reversal_pattern: str | None,
    trend_strength: float | None,  # 0-1
    retest_held: bool,
    relative_strength_vs_nifty: float | None,
    market_regime: str,
    sector_confirmed: bool,
    min_rvol: float = 1.1,  # was 1.3 — synced with dip_buy_engine.MIN_RVOL_CONFIRM, see that file
) -> ScoreResult:
    breakdown: dict[str, float] = {}

    breakdown["support_hold_quality"] = _weighted_component(
        bool(support_hold_quality and support_hold_quality > 0), DIP_BUY_WEIGHTS["support_hold_quality"], support_hold_quality or 0
    )

    rvol_quality = min((rvol or 0) / (min_rvol * 2), 1.0) if rvol else 0.0
    breakdown["relative_volume"] = _weighted_component(bool(rvol and rvol >= min_rvol), DIP_BUY_WEIGHTS["relative_volume"], rvol_quality)

    pattern_quality = {"MORNING_STAR": 1.0, "BULLISH_ENGULFING": 0.85, "HAMMER": 0.7}.get(reversal_pattern or "", 0.0)
    breakdown["reversal_candle_quality"] = _weighted_component(reversal_pattern is not None, DIP_BUY_WEIGHTS["reversal_candle_quality"], pattern_quality)

    breakdown["trend"] = _weighted_component(bool(trend_strength and trend_strength > 0), DIP_BUY_WEIGHTS["trend"], trend_strength or 0)
    breakdown["retest"] = _weighted_component(retest_held, DIP_BUY_WEIGHTS["retest"])

    rs_quality = min(max((relative_strength_vs_nifty or 0) / 10.0, 0), 1.0)
    breakdown["relative_strength"] = _weighted_component(bool(relative_strength_vs_nifty and relative_strength_vs_nifty > 0), DIP_BUY_WEIGHTS["relative_strength"], rs_quality)

    breakdown["market_confirmation"] = _weighted_component(market_regime in (BULL, STRONG_BULL), DIP_BUY_WEIGHTS["market_confirmation"])
    breakdown["sector_confirmation"] = _weighted_component(sector_confirmed, DIP_BUY_WEIGHTS["sector_confirmation"])

    raw_total = sum(breakdown.values())
    regime_adjusted = raw_total * REGIME_SCORE_MULTIPLIER.get(market_regime, 1.0)
    total = min(regime_adjusted, 100.0)

    return ScoreResult(total=total, tier=score_tier(total), breakdown=breakdown)


# --- Hard rejection rules (override score, not just downgrade) ---
# These mirror the state-machine's own NOT_CONFIRMED/INVALIDATED transitions
# (breakout_engine.py / dip_buy_engine.py) — kept here too as an explicit,
# testable checklist matching docs/engine.md's "Hard rejection rules" table,
# for callers (e.g. backtest metrics) that want to audit why a signal never
# reached CONFIRMED without re-deriving it from the state machine.


def breakout_rejection_reason(
    *, closed_above_resistance: bool, rvol: float | None, min_rvol: float, upper_wick_pct: float, closed_back_below_level: bool
) -> str | None:
    if not closed_above_resistance:
        return "NOT_CONFIRMED_NO_CLOSE_ABOVE_RESISTANCE"
    if rvol is None or rvol < min_rvol:
        return "RVOL_BELOW_MINIMUM"
    if upper_wick_pct > 60.0:
        return "SEVERE_REJECTION_WICK"
    if closed_back_below_level:
        return "INVALIDATED_CLOSE_BELOW_BREAKOUT_LEVEL"
    return None


def dip_buy_rejection_reason(
    *, uptrend_precondition_met: bool, closed_below_support: bool, reversal_rvol: float | None, min_rvol: float, made_new_low_after_confirmed: bool
) -> str | None:
    if not uptrend_precondition_met:
        return "UPTREND_PRECONDITION_NOT_MET"
    if closed_below_support:
        return "NOT_CONFIRMED_CLOSED_BELOW_SUPPORT"
    if reversal_rvol is None or reversal_rvol < min_rvol:
        return "REVERSAL_RVOL_BELOW_MINIMUM"
    if made_new_low_after_confirmed:
        return "INVALIDATED_NEW_LOW_BELOW_DIP"
    return None
