"""Prior day/week/month high-low, 52W high-low, swing high/low, resistance clustering.

See docs/engine.md#modules. All functions take bar_history truncated to
"today" by the caller — no look-ahead here either.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Level:
    level_type: str
    price: float
    strength: float | None = None


def prior_period_high_low(bar_history: pd.DataFrame, period: str) -> tuple[float | None, float | None]:
    """period: 'D' (prior day), 'W' (prior week), 'ME' (prior month)."""
    if bar_history.empty:
        return None, None

    df = bar_history.copy()
    df.index = pd.to_datetime(df.index)
    grouped = df.resample(period).agg({"high": "max", "low": "min"})
    if len(grouped) < 2:
        return None, None
    prior = grouped.iloc[-2]
    return float(prior["high"]), float(prior["low"])


def fifty_two_week_high_low(bar_history: pd.DataFrame) -> tuple[float | None, float | None]:
    if bar_history.empty:
        return None, None
    window = bar_history.tail(252)
    return float(window["high"].max()), float(window["low"].min())


def swing_highs_lows(bar_history: pd.DataFrame, lookback: int = 3) -> list[Level]:
    """Fractal-style swing points: a bar strictly higher/lower than every
    other bar within `lookback` bars on each side. Uses only bars already in
    bar_history (which the caller has truncated to "today"), so the most
    recent `lookback` bars can't yet be confirmed as swings — that's
    expected and correct, not a bug.

    Strict comparison (not >=) is deliberate: on flat/tied data (e.g. a
    quiet consolidation where several bars share the same high), a >=
    comparison would flag every tied bar as its own "swing high", producing
    a degenerate high-strength cluster at the tied price that isn't a real
    resistance level — only a genuinely standalone local extreme counts.
    """
    if len(bar_history) < (2 * lookback + 1):
        return []

    highs = bar_history["high"].values
    lows = bar_history["low"].values
    dates = bar_history.index

    levels: list[Level] = []
    for i in range(lookback, len(bar_history) - lookback):
        window_high = highs[i - lookback : i + lookback + 1]
        window_low = lows[i - lookback : i + lookback + 1]
        others_high = np.delete(window_high, lookback)
        others_low = np.delete(window_low, lookback)
        if highs[i] > others_high.max():
            levels.append(Level(level_type="SWING_HIGH", price=float(highs[i])))
        if lows[i] < others_low.min():
            levels.append(Level(level_type="SWING_LOW", price=float(lows[i])))

    return levels


def resistance_clusters(bar_history: pd.DataFrame, tolerance_pct: float = 0.5, min_touches: int = 2) -> list[Level]:
    """Group swing highs within tolerance_pct of each other into clusters;
    strength = touch count. Simple O(n^2) clustering — universe-sized daily
    data, not performance-critical.
    """
    swings = [lvl for lvl in swing_highs_lows(bar_history) if lvl.level_type == "SWING_HIGH"]
    if not swings:
        return []

    prices = sorted(lvl.price for lvl in swings)
    clusters: list[list[float]] = []
    for price in prices:
        placed = False
        for cluster in clusters:
            if abs(price - cluster[-1]) / cluster[-1] * 100 <= tolerance_pct:
                cluster.append(price)
                placed = True
                break
        if not placed:
            clusters.append([price])

    return [
        Level(level_type="RESISTANCE_CLUSTER", price=sum(c) / len(c), strength=float(len(c)))
        for c in clusters
        if len(c) >= min_touches
    ]


def support_clusters(bar_history: pd.DataFrame, tolerance_pct: float = 0.5, min_touches: int = 2) -> list[Level]:
    """Same as resistance_clusters but over swing lows — used by dip-buy's
    SUPPORT_TEST state (docs/engine.md#dip-buy-setup).
    """
    swings = [lvl for lvl in swing_highs_lows(bar_history) if lvl.level_type == "SWING_LOW"]
    if not swings:
        return []

    prices = sorted(lvl.price for lvl in swings)
    clusters: list[list[float]] = []
    for price in prices:
        placed = False
        for cluster in clusters:
            if abs(price - cluster[-1]) / cluster[-1] * 100 <= tolerance_pct:
                cluster.append(price)
                placed = True
                break
        if not placed:
            clusters.append([price])

    return [
        Level(level_type="SUPPORT_CLUSTER", price=sum(c) / len(c), strength=float(len(c)))
        for c in clusters
        if len(c) >= min_touches
    ]


def resistance_levels(bar_history: pd.DataFrame) -> list[Level]:
    """All resistance-side levels for breakout_engine.py: prior day/week/month
    high, 52W high, swing highs, and resistance clusters — everything a
    breakout detector needs to find its trigger level, in one call.

    bar_history: OHLCV DataFrame indexed by date ascending, sliced to "today".
    """
    levels: list[Level] = []

    prior_day_high, _ = prior_period_high_low(bar_history, "D")
    if prior_day_high is not None:
        levels.append(Level(level_type="PRIOR_DAY_HIGH", price=prior_day_high))

    prior_week_high, _ = prior_period_high_low(bar_history, "W")
    if prior_week_high is not None:
        levels.append(Level(level_type="PRIOR_WEEK_HIGH", price=prior_week_high))

    prior_month_high, _ = prior_period_high_low(bar_history, "ME")
    if prior_month_high is not None:
        levels.append(Level(level_type="PRIOR_MONTH_HIGH", price=prior_month_high))

    high_52w, _ = fifty_two_week_high_low(bar_history)
    if high_52w is not None:
        levels.append(Level(level_type="52W_HIGH", price=high_52w))

    levels.extend(lvl for lvl in swing_highs_lows(bar_history) if lvl.level_type == "SWING_HIGH")
    levels.extend(resistance_clusters(bar_history))

    return levels


def support_levels(bar_history: pd.DataFrame) -> list[Level]:
    """All support-side levels for dip_buy_engine.py: prior day/week/month
    low, 52W low, swing lows, and support clusters — everything a dip-buy
    detector needs to evaluate SUPPORT_TEST against, in one call.

    bar_history: OHLCV DataFrame indexed by date ascending, sliced to "today".
    """
    levels: list[Level] = []

    _, prior_day_low = prior_period_high_low(bar_history, "D")
    if prior_day_low is not None:
        levels.append(Level(level_type="PRIOR_DAY_LOW", price=prior_day_low))

    _, prior_week_low = prior_period_high_low(bar_history, "W")
    if prior_week_low is not None:
        levels.append(Level(level_type="PRIOR_WEEK_LOW", price=prior_week_low))

    _, prior_month_low = prior_period_high_low(bar_history, "ME")
    if prior_month_low is not None:
        levels.append(Level(level_type="PRIOR_MONTH_LOW", price=prior_month_low))

    _, low_52w = fifty_two_week_high_low(bar_history)
    if low_52w is not None:
        levels.append(Level(level_type="52W_LOW", price=low_52w))

    levels.extend(lvl for lvl in swing_highs_lows(bar_history) if lvl.level_type == "SWING_LOW")
    levels.extend(support_clusters(bar_history))

    return levels
