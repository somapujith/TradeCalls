"""Bullish reversal candle pattern detection: hammer, bullish engulfing,
morning star — needed by dip_buy_engine.py's REVERSAL_CANDLE state
(docs/engine.md#dip-buy-setup).

Detection looks only at the trailing window ending at the most recent bar
("today") — never at bars beyond it, consistent with the engine's
no-look-ahead requirement.
"""
from __future__ import annotations

import pandas as pd

HAMMER = "HAMMER"
BULLISH_ENGULFING = "BULLISH_ENGULFING"
MORNING_STAR = "MORNING_STAR"

HAMMER_LOWER_WICK_MULT = 2.0
HAMMER_UPPER_WICK_MAX_PCT = 0.25
DOJI_BODY_MAX_PCT = 0.1
STAR_GAP_BODY_MAX_PCT = 0.3


def _body(bar: pd.Series) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _range(bar: pd.Series) -> float:
    return float(bar["high"]) - float(bar["low"])


class CandleQuality:
    """Body %/close-location metrics for breakout_engine's CANDLE_CONFIRMATION
    state. Plain class (not a dataclass) to keep equality/hashing out of the
    way — callers only ever read .body_pct/.close_location_pct.
    """

    __slots__ = ("body_pct", "close_location_pct")

    def __init__(self, body_pct: float, close_location_pct: float) -> None:
        self.body_pct = body_pct
        self.close_location_pct = close_location_pct


def candle_quality(open_: float, high: float, low: float, close: float) -> CandleQuality:
    """body_pct: candle body as % of the full high-low range.
    close_location_pct: where the close sits in that range, 0 = at low, 100 = at high.
    """
    full_range = high - low
    if full_range <= 0:
        return CandleQuality(body_pct=0.0, close_location_pct=50.0)
    body_pct = abs(close - open_) / full_range * 100
    close_location_pct = (close - low) / full_range * 100
    return CandleQuality(body_pct=body_pct, close_location_pct=close_location_pct)


def upper_wick_pct(open_: float, high: float, low: float, close: float) -> float:
    """Upper rejection wick as % of full range — breakout's hard-rejection
    rule (docs/engine.md: >60% triggers SEVERE_REJECTION_WICK)."""
    full_range = high - low
    if full_range <= 0:
        return 0.0
    body_top = max(open_, close)
    return (high - body_top) / full_range * 100


def _is_bullish(bar: pd.Series) -> bool:
    return float(bar["close"]) > float(bar["open"])


def _is_hammer(bar: pd.Series) -> bool:
    bar_range = _range(bar)
    if bar_range == 0:
        return False
    body = _body(bar)
    lower_wick = min(float(bar["open"]), float(bar["close"])) - float(bar["low"])
    upper_wick = float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))
    return (
        body > 0
        and lower_wick >= HAMMER_LOWER_WICK_MULT * body
        and upper_wick <= HAMMER_UPPER_WICK_MAX_PCT * bar_range
    )


def _is_bullish_engulfing(prev_bar: pd.Series, bar: pd.Series) -> bool:
    if _is_bullish(prev_bar) or not _is_bullish(bar):
        return False
    return float(bar["open"]) <= float(prev_bar["close"]) and float(bar["close"]) >= float(prev_bar["open"])


def _is_morning_star(bar1: pd.Series, bar2: pd.Series, bar3: pd.Series) -> bool:
    if _is_bullish(bar1):
        return False
    bar1_range = _range(bar1)
    bar2_range = _range(bar2)
    if bar1_range == 0 or bar2_range == 0:
        return False

    bar1_body = _body(bar1)
    is_small_middle = _body(bar2) <= DOJI_BODY_MAX_PCT * bar2_range or _body(bar2) < bar1_body
    gapped_down = max(float(bar2["open"]), float(bar2["close"])) < float(bar1["close"]) + STAR_GAP_BODY_MAX_PCT * bar1_body

    if not (is_small_middle and gapped_down):
        return False

    if not _is_bullish(bar3):
        return False

    bar1_midpoint = float(bar1["open"]) - bar1_body / 2 if not _is_bullish(bar1) else float(bar1["close"])
    bar1_midpoint = (float(bar1["open"]) + float(bar1["close"])) / 2
    return float(bar3["close"]) >= bar1_midpoint


def detect_reversal_pattern(bars: pd.DataFrame) -> dict[str, str | None]:
    """Detect which bullish reversal pattern (if any) matched on the most
    recent bar in `bars`.

    bars: OHLCV DataFrame ordered by date ascending, containing at least the
    trailing 3 bars needed for morning-star detection (fewer bars still
    works for hammer/engulfing, which only need 1-2). Only the last bar is
    evaluated as the candidate reversal bar.

    Returns {"pattern": one of HAMMER/BULLISH_ENGULFING/MORNING_STAR or
    None, "bar_date": the matched bar's index value or None}.
    """
    if bars.empty:
        return {"pattern": None, "bar_date": None}

    last = bars.iloc[-1]
    last_date = bars.index[-1]

    if len(bars) >= 3:
        bar1, bar2, bar3 = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
        if _is_morning_star(bar1, bar2, bar3):
            return {"pattern": MORNING_STAR, "bar_date": last_date}

    if len(bars) >= 2:
        prev = bars.iloc[-2]
        if _is_bullish_engulfing(prev, last):
            return {"pattern": BULLISH_ENGULFING, "bar_date": last_date}

    if _is_hammer(last):
        return {"pattern": HAMMER, "bar_date": last_date}

    return {"pattern": None, "bar_date": None}
