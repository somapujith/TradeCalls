"""Relative volume helper — not a named module in docs/engine.md but required
by both engines' VOLUME_CONFIRMATION states and scoring.py's RVOL component.
Kept small and colocated with breakout/ since that's its primary consumer.
"""
from __future__ import annotations

import pandas as pd


def relative_volume(volume_history: pd.Series, lookback: int = 20) -> float | None:
    """Today's volume (last element) vs trailing lookback-day average
    (excluding today). Caller truncates volume_history to "today".
    """
    if len(volume_history) < lookback + 1:
        return None
    today = volume_history.iloc[-1]
    baseline = volume_history.iloc[-(lookback + 1) : -1].mean()
    if baseline <= 0:
        return None
    return float(today / baseline)
