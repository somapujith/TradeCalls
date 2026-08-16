"""Tests for app.breakout.target_ladder — shared by breakout_engine.py and
dip_buy_engine.py (was duplicated identically in both, now one place)."""
from __future__ import annotations

import pytest

from app.breakout.target_ladder import MIN_TARGET_PCT, target_ladder
from app.market.levels import Level


def test_wide_risk_uses_raw_r_multiple_unfloored():
    """When the structural stop is wide enough that raw R-multiples already
    clear the % floor, the raw value wins (floor doesn't cap it down)."""
    entry, stop = 100.0, 70.0  # 30% risk — 1R alone is already a 30% move
    ladder = target_ladder(entry, stop, [])

    assert ladder["target_1r"] == pytest.approx(130.0)
    assert ladder["target_3r"] == pytest.approx(190.0)


def test_tight_risk_is_floored_at_minimum_pct():
    """A very tight structural stop (1% risk) would produce a 1% target_1r
    raw — floored up to the minimum instead."""
    entry, stop = 100.0, 99.0  # 1% risk
    ladder = target_ladder(entry, stop, [])

    for key, min_pct in MIN_TARGET_PCT.items():
        assert ladder[key] == pytest.approx(entry * (1 + min_pct))


def test_targets_remain_monotonically_increasing_when_floored():
    entry, stop = 100.0, 99.5  # very tight, all rungs floored
    ladder = target_ladder(entry, stop, [])

    assert ladder["target_1r"] < ladder["target_1_5r"] < ladder["target_2r"] < ladder["target_3r"]


def test_nearest_structural_target_picks_closest_resistance_above_entry():
    entry, stop = 100.0, 90.0
    clusters = [Level(price=150.0, level_type="far"), Level(price=110.0, level_type="near"), Level(price=95.0, level_type="below")]

    ladder = target_ladder(entry, stop, clusters)

    assert ladder["nearest_structural_target"] == pytest.approx(110.0)


def test_nearest_structural_target_none_when_nothing_above_entry():
    ladder = target_ladder(100.0, 90.0, [Level(price=95.0, level_type="below")])

    assert ladder["nearest_structural_target"] is None


def test_nearest_structural_target_none_when_no_clusters():
    ladder = target_ladder(100.0, 90.0, [])

    assert ladder["nearest_structural_target"] is None
