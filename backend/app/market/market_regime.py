"""Market regime classification: STRONG_BULL..STRONG_BEAR from NIFTY/BANKNIFTY
trend + India VIX (or realized volatility as a free fallback proxy).

See docs/engine.md#modules. yfinance's "^INDIAVIX" ticker was verified
against this environment's network access and returned no data — Yahoo's
chart endpoint refused/rate-limited the request (JSONDecodeError on an
empty response body), which may be an environment/network restriction here
rather than the ticker being permanently unavailable. Since the ticker's
reliability could not be confirmed, this module always computes NIFTY
realized volatility as the primary vol signal and only tries India VIX as
an optional override when a caller supplies it explicitly — it never
fetches network data itself (data/yfinance_client.py owns all yfinance
calls; this module is pure computation over bars the caller provides).
"""
from __future__ import annotations

import pandas as pd

STRONG_BULL = "STRONG_BULL"
BULL = "BULL"
NEUTRAL = "NEUTRAL"
BEAR = "BEAR"
STRONG_BEAR = "STRONG_BEAR"

TREND_LOOKBACK_DAYS = 20
VOL_LOOKBACK_DAYS = 20
TRADING_DAYS_PER_YEAR = 252

STRONG_TREND_PCT = 5.0
MILD_TREND_PCT = 1.5
HIGH_VOL_ANNUALIZED_PCT = 20.0


def _trend_return_pct(close: pd.Series, lookback: int = TREND_LOOKBACK_DAYS) -> float | None:
    if len(close) <= lookback:
        return None
    start_price = float(close.iloc[-(lookback + 1)])
    end_price = float(close.iloc[-1])
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price * 100


def realized_volatility_pct(close: pd.Series, lookback: int = VOL_LOOKBACK_DAYS) -> float | None:
    """Annualized realized volatility (%) over the trailing `lookback` daily
    returns — free proxy for India VIX per engine.md when live VIX data
    isn't available.
    """
    if len(close) <= lookback:
        return None
    returns = close.pct_change().dropna().tail(lookback)
    if returns.empty:
        return None
    daily_std = float(returns.std())
    return daily_std * (TRADING_DAYS_PER_YEAR ** 0.5) * 100


def classify_market_regime(
    nifty_close: pd.Series,
    banknifty_close: pd.Series | None = None,
    india_vix_close: pd.Series | None = None,
) -> dict[str, object]:
    """Classify current market regime from NIFTY (required) and BANKNIFTY
    (optional, corroborating) trend, using volatility (India VIX if the
    caller supplies it, else realized vol computed from nifty_close) as a
    dampener on the trend-only classification.

    All series must be truncated to "today" and ascending by date. Returns
    {"regime": one of the module-level enum strings, "nifty_trend_pct",
    "vix_or_realized_vol_pct", "vol_source"}.
    """
    nifty_trend = _trend_return_pct(nifty_close)

    if india_vix_close is not None and not india_vix_close.empty:
        vol_pct = float(india_vix_close.iloc[-1])
        vol_source = "INDIA_VIX"
    else:
        vol_pct = realized_volatility_pct(nifty_close)
        vol_source = "REALIZED_VOL"

    if nifty_trend is None:
        return {
            "regime": NEUTRAL,
            "nifty_trend_pct": None,
            "vix_or_realized_vol_pct": vol_pct,
            "vol_source": vol_source,
        }

    banknifty_trend = _trend_return_pct(banknifty_close) if banknifty_close is not None else None
    high_vol = vol_pct is not None and vol_pct >= HIGH_VOL_ANNUALIZED_PCT

    if nifty_trend >= STRONG_TREND_PCT:
        regime = BULL if high_vol else STRONG_BULL
    elif nifty_trend >= MILD_TREND_PCT:
        regime = NEUTRAL if high_vol else BULL
    elif nifty_trend <= -STRONG_TREND_PCT:
        regime = BEAR if high_vol else STRONG_BEAR
    elif nifty_trend <= -MILD_TREND_PCT:
        regime = NEUTRAL if high_vol else BEAR
    else:
        regime = NEUTRAL

    if banknifty_trend is not None:
        both_negative = nifty_trend < 0 and banknifty_trend < 0
        both_positive = nifty_trend > 0 and banknifty_trend > 0
        if regime in (STRONG_BULL, BULL) and not both_positive:
            regime = NEUTRAL
        elif regime in (STRONG_BEAR, BEAR) and not both_negative:
            regime = NEUTRAL

    return {
        "regime": regime,
        "nifty_trend_pct": nifty_trend,
        "vix_or_realized_vol_pct": vol_pct,
        "vol_source": vol_source,
    }
