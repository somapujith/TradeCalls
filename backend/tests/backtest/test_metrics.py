"""Tests for app.backtest.metrics.

Coverage: win_rate/profit_factor only counting closed trades (excluding
still-open SESSION_END rows), profit_factor returning None (not inf) when
gross_loss == 0, empty-trades handling, and breakdown_by_score_bucket
bucketing lining up with app.breakout.scoring.score_tier's thresholds.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.backtest.metrics import compute_backtest_metrics
from app.breakout.scoring import score_tier


def _trade(
    exit_reason: str,
    pnl: float = 0.0,
    score: float = 75.0,
    sector: str = "IT",
    entry_date: str = "2024-01-02",
    mfe: float = 0.0,
    mae: float = 0.0,
    holding_days: int | None = 1,
    regime: str = "BULL",
) -> dict:
    return {
        "exit_reason": exit_reason,
        "pnl": pnl,
        "score": score,
        "sector": sector,
        "entry_date": entry_date,
        "mfe": mfe,
        "mae": mae,
        "holding_days": holding_days,
        "regime": regime,
    }


def test_empty_trades_returns_zeroed_metrics_with_none_rates():
    result = compute_backtest_metrics(pd.DataFrame())

    assert result.total_trades == 0
    assert result.win_rate is None
    assert result.profit_factor is None
    assert result.avg_mfe is None
    assert result.avg_mae is None
    assert result.avg_holding_days is None
    assert result.breakdown_by_score_bucket == {}


def test_total_trades_counts_all_rows_including_session_end():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100),
            _trade("INVALIDATED", pnl=-50),
            _trade("SESSION_END", pnl=0, holding_days=None),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.total_trades == 3


def test_win_rate_excludes_session_end_still_open_trades():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100),
            _trade("SESSION_END", pnl=0, holding_days=None),
            _trade("SESSION_END", pnl=0, holding_days=None),
        ]
    )

    result = compute_backtest_metrics(trades)

    # Only 1 closed trade (the TARGET_HIT), and it's a win -> win_rate 1.0,
    # not diluted by the 2 still-open SESSION_END rows.
    assert result.win_rate == pytest.approx(1.0)


def test_win_rate_counts_target_hit_as_win_and_invalidated_as_loss():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100),
            _trade("TARGET_HIT", pnl=50),
            _trade("INVALIDATED", pnl=-30),
            _trade("INVALIDATED", pnl=-20),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.win_rate == pytest.approx(0.5)


def test_win_rate_none_when_only_session_end_trades_present():
    trades = pd.DataFrame([_trade("SESSION_END", pnl=0, holding_days=None)])

    result = compute_backtest_metrics(trades)

    assert result.win_rate is None


def test_profit_factor_excludes_session_end_trades():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=200),
            _trade("INVALIDATED", pnl=-100),
            _trade("SESSION_END", pnl=100_000, holding_days=None),  # must be ignored
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.profit_factor == pytest.approx(2.0)


def test_profit_factor_is_none_not_infinity_when_gross_loss_is_zero():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100),
            _trade("TARGET_HIT", pnl=50),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.profit_factor is None
    assert result.profit_factor != float("inf")


def test_profit_factor_none_when_no_pnl_column():
    trades = pd.DataFrame([{"exit_reason": "TARGET_HIT"}])

    result = compute_backtest_metrics(trades)

    assert result.profit_factor is None


def test_profit_factor_computed_correctly_with_mixed_wins_and_losses():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=300),
            _trade("TARGET_HIT", pnl=100),
            _trade("INVALIDATED", pnl=-150),
            _trade("INVALIDATED", pnl=-50),
        ]
    )

    result = compute_backtest_metrics(trades)

    # gross_profit=400, gross_loss=200 -> profit_factor=2.0
    assert result.profit_factor == pytest.approx(2.0)


def test_avg_mfe_avg_mae_avg_holding_days_exclude_session_end():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100, mfe=10.0, mae=-2.0, holding_days=3),
            _trade("INVALIDATED", pnl=-30, mfe=5.0, mae=-8.0, holding_days=1),
            _trade("SESSION_END", pnl=0, mfe=999.0, mae=-999.0, holding_days=None),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.avg_mfe == pytest.approx(7.5)
    assert result.avg_mae == pytest.approx(-5.0)
    assert result.avg_holding_days == pytest.approx(2.0)


def test_score_bucket_boundaries_match_score_tier_thresholds():
    """Sanity-check that scoring.score_tier's own thresholds are what we
    expect, so the metrics breakdown assertions below are meaningful."""
    assert score_tier(95) == "A+"
    assert score_tier(90) == "A+"
    assert score_tier(89.9) == "A"
    assert score_tier(80) == "A"
    assert score_tier(79.9) == "B"
    assert score_tier(70) == "B"
    assert score_tier(69.9) == "C"
    assert score_tier(60) == "C"
    assert score_tier(59.9) == "IGNORE"


def test_breakdown_by_score_bucket_groups_trades_by_score_tier():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100, score=95.0),  # A+
            _trade("TARGET_HIT", pnl=100, score=91.0),  # A+
            _trade("INVALIDATED", pnl=-50, score=85.0),  # A
            _trade("TARGET_HIT", pnl=100, score=65.0),  # C
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.breakdown_by_score_bucket["A+"]["trades"] == 2
    assert result.breakdown_by_score_bucket["A+"]["win_rate"] == pytest.approx(1.0)
    assert result.breakdown_by_score_bucket["A"]["trades"] == 1
    assert result.breakdown_by_score_bucket["A"]["win_rate"] == pytest.approx(0.0)
    assert result.breakdown_by_score_bucket["C"]["trades"] == 1
    assert result.breakdown_by_score_bucket["C"]["win_rate"] == pytest.approx(1.0)


def test_breakdown_by_score_bucket_excludes_session_end_rows():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100, score=95.0),
            _trade("SESSION_END", pnl=0, score=95.0, holding_days=None),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.breakdown_by_score_bucket["A+"]["trades"] == 1


def test_breakdown_by_sector_groups_correctly():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100, sector="IT"),
            _trade("INVALIDATED", pnl=-50, sector="IT"),
            _trade("TARGET_HIT", pnl=100, sector="BANKING"),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.breakdown_by_sector["IT"]["trades"] == 2
    assert result.breakdown_by_sector["IT"]["win_rate"] == pytest.approx(0.5)
    assert result.breakdown_by_sector["BANKING"]["trades"] == 1
    assert result.breakdown_by_sector["BANKING"]["win_rate"] == pytest.approx(1.0)


def test_breakdown_by_day_of_week_derived_from_entry_date():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100, entry_date="2024-01-02"),  # Tuesday
            _trade("INVALIDATED", pnl=-50, entry_date="2024-01-02"),  # Tuesday
        ]
    )

    result = compute_backtest_metrics(trades)

    assert "Tuesday" in result.breakdown_by_day_of_week
    assert result.breakdown_by_day_of_week["Tuesday"]["trades"] == 2


def test_breakdown_by_regime_groups_correctly():
    trades = pd.DataFrame(
        [
            _trade("TARGET_HIT", pnl=100, regime="BULL"),
            _trade("INVALIDATED", pnl=-50, regime="BEAR"),
        ]
    )

    result = compute_backtest_metrics(trades)

    assert result.breakdown_by_regime["BULL"]["trades"] == 1
    assert result.breakdown_by_regime["BEAR"]["trades"] == 1


def test_breakdown_missing_column_returns_empty_dict():
    trades = pd.DataFrame([{"exit_reason": "TARGET_HIT", "pnl": 100}])

    result = compute_backtest_metrics(trades)

    assert result.breakdown_by_sector == {}
    assert result.breakdown_by_regime == {}
