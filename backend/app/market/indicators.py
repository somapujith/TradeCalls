"""EMA9/20/50, SMA100/200, RSI, MACD, ATR, Bollinger Bands.

Computed from bar history truncated to "today" by the caller — this module
never looks past the last row it is given, which is the no-look-ahead
guarantee required by docs/engine.md (unit-tested elsewhere by asserting
output is unchanged when future bars are appended to the input).

pandas-ta==0.3.14b0 (pinned in requirements.txt) has no distribution
installable against Python 3.11 as of this writing (PyPI only publishes
0.4.67b0+ for py311, which require Python >=3.12) — indicators are
implemented directly with pandas rolling/ewm math instead of importing
pandas_ta, so this module has no hard runtime dependency on it.
"""
from __future__ import annotations

import pandas as pd

INDICATOR_COLUMNS = [
    "ema9",
    "ema20",
    "ema50",
    "sma100",
    "sma200",
    "rsi",
    "macd",
    "macd_signal",
    "atr",
    "bb_upper",
    "bb_lower",
]

RSI_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLLINGER_PERIOD = 20
BOLLINGER_STD_MULT = 2


def compute_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """bars: DataFrame ordered by date ascending, columns [open, high, low,
    close, volume], already truncated to "today" by the caller.

    Returns a DataFrame aligned to bars.index with columns matching
    TechnicalIndicator's model columns exactly (see INDICATOR_COLUMNS), one
    row per input row. Rows where a rolling window isn't yet full are NaN —
    that's correct, not a bug.
    """
    if bars.empty:
        return pd.DataFrame(columns=INDICATOR_COLUMNS)

    close = bars["close"]
    high = bars["high"]
    low = bars["low"]

    out = pd.DataFrame(index=bars.index)
    out["ema9"] = _ema(close, 9)
    out["ema20"] = _ema(close, 20)
    out["ema50"] = _ema(close, 50)
    out["sma100"] = close.rolling(window=100, min_periods=100).mean()
    out["sma200"] = close.rolling(window=200, min_periods=200).mean()
    out["rsi"] = _rsi(close, RSI_PERIOD)

    macd_line = _ema(close, MACD_FAST) - _ema(close, MACD_SLOW)
    out["macd"] = macd_line
    out["macd_signal"] = _ema(macd_line, MACD_SIGNAL)

    out["atr"] = _atr(high, low, close, ATR_PERIOD)

    bb_mid = close.rolling(window=BOLLINGER_PERIOD, min_periods=BOLLINGER_PERIOD).mean()
    bb_std = close.rolling(window=BOLLINGER_PERIOD, min_periods=BOLLINGER_PERIOD).std()
    out["bb_upper"] = bb_mid + BOLLINGER_STD_MULT * bb_std
    out["bb_lower"] = bb_mid - BOLLINGER_STD_MULT * bb_std

    return out[INDICATOR_COLUMNS]


def latest_indicator_row(bars: pd.DataFrame) -> dict[str, float | None]:
    """Convenience wrapper: compute_indicators(bars) then return only the
    last row as a dict, for callers (e.g. the daily scheduler job) that only
    need "today"'s values rather than the full aligned history.
    """
    computed = compute_indicators(bars)
    if computed.empty:
        return {col: None for col in INDICATOR_COLUMNS}

    last_row = computed.iloc[-1]
    return {col: (None if pd.isna(last_row[col]) else float(last_row[col])) for col in INDICATOR_COLUMNS}


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
