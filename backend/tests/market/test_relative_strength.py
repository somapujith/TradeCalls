"""Tests for app.market.relative_strength.

NOTE ON API MISMATCH: app/backtest/simulator.py imports
`compute_relative_strength` from this module, and the task brief describes
a `RelativeStrength` dataclass returned by a `compute_relative_strength(...)
-> RelativeStrength | None` function. Neither exists in the current
app/market/relative_strength.py — the module currently exposes a single
function named `relative_strength(stock_close, sector_close, nifty_close,
lookback)` (note argument order: sector before nifty) that returns a plain
dict, never None (missing data yields None-valued dict keys instead of a
None return). These tests exercise the actual current function/signature;
see the task report for the bug writeup rather than a source fix here.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.market.relative_strength import LOOKBACK_DAYS, relative_strength


def _growth_series(start_price: float, daily_pct: float, n: int) -> pd.Series:
    prices = [start_price]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + daily_pct / 100))
    idx = pd.date_range(start=date(2024, 1, 1), periods=n, freq="D")
    return pd.Series(prices, index=idx)


# --- happy path --------------------------------------------------------


def test_relative_strength_returns_expected_dict_shape():
    stock = _growth_series(100, 0.5, 25)
    nifty = _growth_series(1000, 0.2, 25)
    sector = _growth_series(500, 0.3, 25)

    result = relative_strength(stock, sector, nifty, lookback=20)

    assert set(result.keys()) == {
        "stock_return",
        "sector_return",
        "nifty_return",
        "rs_vs_sector",
        "rs_vs_nifty",
    }


def test_relative_strength_stock_outperforming_nifty_has_positive_rs():
    stock = _growth_series(100, 1.0, 25)  # strong up move
    nifty = _growth_series(1000, 0.1, 25)  # mild up move

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["rs_vs_nifty"] > 0
    assert result["stock_return"] > result["nifty_return"]


def test_relative_strength_stock_underperforming_nifty_has_negative_rs():
    stock = _growth_series(100, -0.5, 25)
    nifty = _growth_series(1000, 0.5, 25)

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["rs_vs_nifty"] < 0


def test_relative_strength_uses_default_lookback_when_not_specified():
    stock = _growth_series(100, 0.5, 25)
    nifty = _growth_series(1000, 0.2, 25)

    result_default = relative_strength(stock, None, nifty)
    result_explicit = relative_strength(stock, None, nifty, lookback=LOOKBACK_DAYS)

    assert result_default == result_explicit


# --- sector_close is None ------------------------------------------------


def test_relative_strength_sector_none_yields_none_sector_fields():
    stock = _growth_series(100, 0.5, 25)
    nifty = _growth_series(1000, 0.2, 25)

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["sector_return"] is None
    assert result["rs_vs_sector"] is None
    # nifty-side fields must still be populated
    assert result["rs_vs_nifty"] is not None


def test_relative_strength_sector_provided_yields_populated_sector_fields():
    stock = _growth_series(100, 0.5, 25)
    nifty = _growth_series(1000, 0.2, 25)
    sector = _growth_series(500, 0.1, 25)

    result = relative_strength(stock, sector, nifty, lookback=20)

    assert result["sector_return"] is not None
    assert result["rs_vs_sector"] is not None


# --- edge cases: empty / too-short history --------------------------------


def test_relative_strength_empty_stock_series_returns_none_stock_fields():
    stock = pd.Series([], dtype=float)
    nifty = _growth_series(1000, 0.2, 25)

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["stock_return"] is None
    assert result["rs_vs_nifty"] is None  # can't compute without stock_return
    assert result["nifty_return"] is not None  # nifty side is independent


def test_relative_strength_too_short_history_returns_none_for_that_series():
    # exactly `lookback` bars is NOT enough (needs > lookback, i.e. lookback+1)
    stock = _growth_series(100, 0.5, 20)
    nifty = _growth_series(1000, 0.2, 25)

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["stock_return"] is None
    assert result["rs_vs_nifty"] is None


def test_relative_strength_exactly_lookback_plus_one_bars_is_sufficient():
    stock = _growth_series(100, 0.5, 21)
    nifty = _growth_series(1000, 0.2, 25)

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["stock_return"] is not None


def test_relative_strength_all_series_too_short_returns_all_none():
    stock = _growth_series(100, 0.5, 5)
    nifty = _growth_series(1000, 0.2, 5)
    sector = _growth_series(500, 0.1, 5)

    result = relative_strength(stock, sector, nifty, lookback=20)

    assert result["stock_return"] is None
    assert result["sector_return"] is None
    assert result["nifty_return"] is None
    assert result["rs_vs_sector"] is None
    assert result["rs_vs_nifty"] is None


def test_relative_strength_zero_start_price_returns_none_for_that_series():
    # _period_return reads start_price from close.iloc[-(lookback+1)]; with
    # lookback=20 and a 21-bar series that's index 0 — put the zero there
    # so it actually lands on the window's start price, not an untouched
    # earlier bar the function never reads.
    idx = pd.date_range(date(2024, 1, 1), periods=21)
    stock = pd.Series([0.0] + [100.0] * 20, index=idx)
    nifty = _growth_series(1000, 0.2, 21)

    result = relative_strength(stock, None, nifty, lookback=20)

    assert result["stock_return"] is None
    assert result["rs_vs_nifty"] is None


# --- No-look-ahead invariant -----------------------------------------------


def test_no_look_ahead_relative_strength_stable_at_truncation_point():
    stock_today = _growth_series(100, 0.4, 30)
    nifty_today = _growth_series(1000, 0.15, 30)
    sector_today = _growth_series(500, 0.25, 30)

    result_today = relative_strength(stock_today, sector_today, nifty_today, lookback=20)

    future_idx = pd.date_range(stock_today.index[-1] + pd.Timedelta(days=1), periods=8)
    stock_future = pd.Series([9999.0] * 8, index=future_idx)
    nifty_future = pd.Series([1.0] * 8, index=future_idx)
    sector_future = pd.Series([5000.0] * 8, index=future_idx)

    stock_extended = pd.concat([stock_today, stock_future])
    nifty_extended = pd.concat([nifty_today, nifty_future])
    sector_extended = pd.concat([sector_today, sector_future])

    truncated_stock = stock_extended.iloc[: len(stock_today)]
    truncated_nifty = nifty_extended.iloc[: len(nifty_today)]
    truncated_sector = sector_extended.iloc[: len(sector_today)]

    result_recomputed = relative_strength(truncated_stock, truncated_sector, truncated_nifty, lookback=20)

    assert result_recomputed == result_today


def test_no_look_ahead_relative_strength_sector_none_stable_at_truncation_point():
    stock_today = _growth_series(100, -0.3, 30)
    nifty_today = _growth_series(1000, 0.1, 30)

    result_today = relative_strength(stock_today, None, nifty_today, lookback=20)

    future_idx = pd.date_range(stock_today.index[-1] + pd.Timedelta(days=1), periods=6)
    stock_future = pd.Series([1.0] * 6, index=future_idx)
    nifty_future = pd.Series([50000.0] * 6, index=future_idx)

    stock_extended = pd.concat([stock_today, stock_future])
    nifty_extended = pd.concat([nifty_today, nifty_future])

    truncated_stock = stock_extended.iloc[: len(stock_today)]
    truncated_nifty = nifty_extended.iloc[: len(nifty_today)]

    result_recomputed = relative_strength(truncated_stock, None, truncated_nifty, lookback=20)

    assert result_recomputed == result_today
