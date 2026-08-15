"""Tests for app.market.market_regime.

NOTE ON API MISMATCH: app/breakout/scoring.py imports `MarketRegime`
(expected to be an enum) and `REGIME_SCORE_MULTIPLIER` (expected to be a
dict) from this module — neither exists in the current
app/market/market_regime.py. The module currently exposes string constants
(STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR) and classify_market_regime()
returning a dict with a "regime" key holding one of those strings, not an
enum member, and there's no score-multiplier table at all. These tests
exercise the actual current API; see the task report for the bug writeup.

Regime classification logic (from the source): nifty_trend_pct is a 20-day
trailing return; vol_pct is India VIX (if supplied) else NIFTY realized
vol; high_vol is vol_pct >= 20.0. BankNifty corroboration, when supplied,
downgrades BULL/STRONG_BULL to NEUTRAL unless BankNifty is also positive,
and downgrades BEAR/STRONG_BEAR to NEUTRAL unless BankNifty is also
negative.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.market.market_regime import (
    BEAR,
    BULL,
    NEUTRAL,
    STRONG_BEAR,
    STRONG_BULL,
    classify_market_regime,
    realized_volatility_pct,
)


def _flat_growth_series(start_price: float, daily_pct: float, n: int) -> pd.Series:
    """Build a close-price series that grows/shrinks by a constant daily %
    with zero day-to-day noise, so realized volatility is ~0 (low vol path).
    """
    prices = [start_price]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + daily_pct / 100))
    idx = pd.date_range(start=date(2024, 1, 1), periods=n, freq="D")
    return pd.Series(prices, index=idx)


def _noisy_series(start_price: float, trend_pct_total: float, n: int, amplitude: float) -> pd.Series:
    """A series with an overall trend but large day-to-day oscillation, to
    drive realized volatility above the 20% annualized threshold.
    """
    import math

    prices = []
    for i in range(n):
        base = start_price * (1 + trend_pct_total / 100 * i / (n - 1))
        wiggle = amplitude * (1 if i % 2 == 0 else -1) * base / 100
        prices.append(base + wiggle)
    idx = pd.date_range(start=date(2024, 1, 1), periods=n, freq="D")
    return pd.Series(prices, index=idx)


# --- realized_volatility_pct -------------------------------------------


def test_realized_volatility_pct_none_when_too_short():
    close = pd.Series([100, 101, 102], index=pd.date_range(date(2024, 1, 1), periods=3))

    result = realized_volatility_pct(close, lookback=20)

    assert result is None


def test_realized_volatility_pct_zero_for_constant_price():
    idx = pd.date_range(date(2024, 1, 1), periods=25)
    close = pd.Series([100.0] * 25, index=idx)

    result = realized_volatility_pct(close, lookback=20)

    assert result == pytest.approx(0.0)


def test_realized_volatility_pct_positive_for_noisy_series():
    close = _noisy_series(100, trend_pct_total=0, n=25, amplitude=3.0)

    result = realized_volatility_pct(close, lookback=20)

    assert result is not None
    assert result > 0


def test_realized_volatility_pct_none_when_returns_empty_after_dropna():
    """A single-valued-then-constant series longer than lookback still has
    >lookback rows, but if every pct_change is NaN (e.g. a single real price
    change followed by nothing else to diff against) returns.dropna() could
    be empty. More directly: an all-NaN close series of the right length
    exercises the same empty-after-dropna guard without relying on that
    specific construction being reachable through public inputs.
    """
    idx = pd.date_range(date(2024, 1, 1), periods=25)
    close = pd.Series([float("nan")] * 25, index=idx)

    result = realized_volatility_pct(close, lookback=20)

    assert result is None


def test_classify_market_regime_zero_start_price_trend_is_none():
    """_trend_return_pct returns None when the trend-window start price is
    exactly 0 (guards a division by zero) — classify_market_regime should
    then fall back to its no-trend-data branch (NEUTRAL) rather than raising.
    """
    idx = pd.date_range(date(2024, 1, 1), periods=25)
    # index -(lookback+1) with lookback=20 on a 25-row series is index 4;
    # place the zero there so _trend_return_pct actually reads it as
    # start_price.
    prices = [100.0] * 4 + [0.0] + [100.0] * 20
    close = pd.Series(prices, index=idx)

    result = classify_market_regime(close)

    assert result["nifty_trend_pct"] is None
    assert result["regime"] == NEUTRAL


# --- classify_market_regime: trend-required-for-classification -------------


def test_classify_market_regime_too_short_history_returns_neutral():
    close = pd.Series([100, 101, 102], index=pd.date_range(date(2024, 1, 1), periods=3))

    result = classify_market_regime(close)

    assert result["regime"] == NEUTRAL
    assert result["nifty_trend_pct"] is None


def test_classify_market_regime_returns_expected_dict_shape():
    close = _flat_growth_series(100, daily_pct=0.0, n=25)

    result = classify_market_regime(close)

    assert set(result.keys()) == {"regime", "nifty_trend_pct", "vix_or_realized_vol_pct", "vol_source"}


# --- five regime buckets, low-vol path (realized vol, no VIX supplied) -----


def test_strong_bull_reachable_with_low_vol():
    # +5% trend over lookback, low realized vol
    close = _flat_growth_series(100, daily_pct=0.25, n=25)  # ~5%+ over 20 days, minimal noise

    result = classify_market_regime(close)

    assert result["nifty_trend_pct"] >= 5.0
    assert result["vix_or_realized_vol_pct"] < 20.0
    assert result["regime"] == STRONG_BULL


def test_bull_reachable_with_mild_trend_low_vol():
    close = _flat_growth_series(100, daily_pct=0.1, n=25)  # ~1.5%-5% trend zone

    result = classify_market_regime(close)

    assert 1.5 <= result["nifty_trend_pct"] < 5.0
    assert result["regime"] == BULL


def test_neutral_reachable_with_flat_trend():
    close = _flat_growth_series(100, daily_pct=0.0, n=25)  # ~0% trend

    result = classify_market_regime(close)

    assert -1.5 < result["nifty_trend_pct"] < 1.5
    assert result["regime"] == NEUTRAL


def test_bear_reachable_with_mild_negative_trend_low_vol():
    close = _flat_growth_series(100, daily_pct=-0.1, n=25)  # -1.5% to -5% zone

    result = classify_market_regime(close)

    assert -5.0 < result["nifty_trend_pct"] <= -1.5
    assert result["regime"] == BEAR


def test_strong_bear_reachable_with_low_vol():
    close = _flat_growth_series(100, daily_pct=-0.3, n=25)  # <= -5% trend

    result = classify_market_regime(close)

    assert result["nifty_trend_pct"] <= -5.0
    assert result["vix_or_realized_vol_pct"] < 20.0
    assert result["regime"] == STRONG_BEAR


# --- high-vol dampening path -------------------------------------------


def test_high_vol_downgrades_strong_bull_to_bull():
    # Strong uptrend overall but with large day-to-day swings -> high realized vol.
    # trend_pct_total is measured start-to-end across all n bars, but
    # _trend_return_pct only looks at the trailing `lookback` (20) of the 25
    # bars here, so ask for comfortably more than 5% to land >= 5.0 in that
    # trailing window too.
    close = _noisy_series(100, trend_pct_total=10.0, n=25, amplitude=5.0)

    result = classify_market_regime(close)

    assert result["nifty_trend_pct"] >= 5.0
    assert result["vix_or_realized_vol_pct"] >= 20.0
    assert result["regime"] == BULL


def test_high_vol_downgrades_strong_bear_to_bear():
    close = _noisy_series(100, trend_pct_total=-6.0, n=25, amplitude=5.0)

    result = classify_market_regime(close)

    assert result["nifty_trend_pct"] <= -5.0
    assert result["vix_or_realized_vol_pct"] >= 20.0
    assert result["regime"] == BEAR


def test_high_vol_downgrades_mild_bull_to_neutral():
    close = _noisy_series(100, trend_pct_total=2.0, n=25, amplitude=5.0)

    result = classify_market_regime(close)

    assert 1.5 <= result["nifty_trend_pct"] < 5.0
    assert result["vix_or_realized_vol_pct"] >= 20.0
    assert result["regime"] == NEUTRAL


# --- India VIX override path (vs realized-vol fallback) --------------------


def test_india_vix_supplied_is_used_instead_of_realized_vol():
    close = _flat_growth_series(100, daily_pct=0.25, n=25)  # would be STRONG_BULL on low realized vol
    vix = pd.Series([25.0], index=[close.index[-1]])  # high VIX supplied explicitly

    result = classify_market_regime(close, india_vix_close=vix)

    assert result["vol_source"] == "INDIA_VIX"
    assert result["vix_or_realized_vol_pct"] == pytest.approx(25.0)
    assert result["regime"] == BULL  # downgraded from STRONG_BULL by high VIX


def test_realized_vol_fallback_used_when_vix_not_supplied():
    close = _flat_growth_series(100, daily_pct=0.25, n=25)

    result = classify_market_regime(close, india_vix_close=None)

    assert result["vol_source"] == "REALIZED_VOL"


def test_realized_vol_fallback_used_when_vix_series_empty():
    close = _flat_growth_series(100, daily_pct=0.25, n=25)
    empty_vix = pd.Series([], dtype=float)

    result = classify_market_regime(close, india_vix_close=empty_vix)

    assert result["vol_source"] == "REALIZED_VOL"


# --- BankNifty corroboration ------------------------------------------------


def test_banknifty_corroborates_bull_when_also_positive():
    nifty = _flat_growth_series(100, daily_pct=0.1, n=25)
    banknifty = _flat_growth_series(200, daily_pct=0.1, n=25)

    result = classify_market_regime(nifty, banknifty_close=banknifty)

    assert result["regime"] == BULL


def test_banknifty_downgrades_bull_to_neutral_when_diverging():
    nifty = _flat_growth_series(100, daily_pct=0.1, n=25)  # positive trend
    banknifty = _flat_growth_series(200, daily_pct=-0.1, n=25)  # negative trend

    result = classify_market_regime(nifty, banknifty_close=banknifty)

    assert result["regime"] == NEUTRAL


def test_banknifty_downgrades_bear_to_neutral_when_diverging():
    nifty = _flat_growth_series(100, daily_pct=-0.1, n=25)  # negative trend
    banknifty = _flat_growth_series(200, daily_pct=0.1, n=25)  # positive trend

    result = classify_market_regime(nifty, banknifty_close=banknifty)

    assert result["regime"] == NEUTRAL


def test_banknifty_none_does_not_affect_classification():
    nifty = _flat_growth_series(100, daily_pct=0.1, n=25)

    result_without = classify_market_regime(nifty, banknifty_close=None)
    result_with_explicit_none = classify_market_regime(nifty)

    assert result_without["regime"] == result_with_explicit_none["regime"]


# --- No-look-ahead invariant -----------------------------------------------


def test_no_look_ahead_classify_market_regime_stable_at_truncation_point():
    close_today = _flat_growth_series(100, daily_pct=0.25, n=25)
    result_today = classify_market_regime(close_today)

    future_idx = pd.date_range(close_today.index[-1] + pd.Timedelta(days=1), periods=10)
    future_prices = pd.Series([1000.0] * 10, index=future_idx)  # dramatic future shock
    close_with_future = pd.concat([close_today, future_prices])

    result_recomputed = classify_market_regime(close_with_future.iloc[: len(close_today)])

    assert result_recomputed == result_today


def test_no_look_ahead_realized_volatility_stable_at_truncation_point():
    close_today = _noisy_series(100, trend_pct_total=3.0, n=25, amplitude=4.0)
    vol_today = realized_volatility_pct(close_today, lookback=20)

    future_idx = pd.date_range(close_today.index[-1] + pd.Timedelta(days=1), periods=5)
    future_prices = pd.Series([9999.0] * 5, index=future_idx)
    close_with_future = pd.concat([close_today, future_prices])

    vol_recomputed = realized_volatility_pct(close_with_future.iloc[: len(close_today)], lookback=20)

    assert vol_recomputed == pytest.approx(vol_today)
