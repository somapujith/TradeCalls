"""ATR-aware entry buffer — shared by breakout (above resistance) and
dip-buy (above reversal candle close), per docs/engine.md.
"""
from __future__ import annotations


def atr_entry_buffer(level: float, atr: float | None, buffer_atr_multiple: float = 0.1) -> float:
    """Entry price = level + a small ATR-scaled buffer, so entries aren't
    triggered by a level being touched exactly. Falls back to a flat 0.1%
    buffer if ATR isn't available yet (early history).
    """
    if atr is None or atr <= 0:
        return level * 1.001
    return level + atr * buffer_atr_multiple
