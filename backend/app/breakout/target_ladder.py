"""Shared R-multiple target ladder for breakout_engine.py and
dip_buy_engine.py — was duplicated identically in both, now one place.

Percentage floor added per user preference (2026-08-17): this is a
positional/delivery-style system (see the "POSITIONAL RESEARCH" Telegram
alert framing), not intraday scalping — stop-loss stays purely structural
(nearest support, unchanged, still disciplined), but a structural stop
that happens to sit very close to entry was producing R-multiple targets
as tight as 1-3% in real backtest data, below what's useful for a
multi-day/week hold. Each rung is floored at a minimum % move so every
call promises a worthwhile delivery-trade profit regardless of how tight
the nearest support happens to be — this loosens the outcome, not the
entry/stop discipline.
"""
from __future__ import annotations

from app.market.levels import Level

MIN_TARGET_PCT = {
    "target_1r": 0.05,
    "target_1_5r": 0.065,
    "target_2r": 0.08,
    "target_3r": 0.10,
}


def target_ladder(entry: float, stop: float, resistance_clusters: list[Level]) -> dict:
    risk = entry - stop
    raw = {
        "target_1r": entry + 1.0 * risk,
        "target_1_5r": entry + 1.5 * risk,
        "target_2r": entry + 2.0 * risk,
        "target_3r": entry + 3.0 * risk,
    }
    ladder = {key: max(value, entry * (1 + MIN_TARGET_PCT[key])) for key, value in raw.items()}

    above = [c for c in resistance_clusters if c.price > entry]
    ladder["nearest_structural_target"] = min(above, key=lambda c: c.price).price if above else None
    return ladder
