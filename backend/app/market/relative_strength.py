"""Stock return vs sector index vs NIFTY — engine.md's "relative strength"
scoring component (11 pts breakout / 10 pts dip-buy per engine.md's tables).

v1 assumption: a simple relative-return comparison over a fixed lookback
window is sufficient — no beta/regression, no volatility normalization.
Documented here since it's a deliberate simplification, not an oversight;
worth revisiting once walk-forward weight tuning exists (engine.md).
"""
from __future__ import annotations

import pandas as pd

LOOKBACK_DAYS = 20


def _period_return(close: pd.Series, lookback: int) -> float | None:
    """Simple (not log) return over the trailing `lookback` bars ending at
    the series' last row — the caller is responsible for truncating close
    to "today" (no-look-ahead is enforced by what's passed in, not here).
    """
    if len(close) <= lookback:
        return None
    start_price = float(close.iloc[-(lookback + 1)])
    end_price = float(close.iloc[-1])
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price


def relative_strength(
    stock_close: pd.Series,
    sector_close: pd.Series | None,
    nifty_close: pd.Series,
    lookback: int = LOOKBACK_DAYS,
) -> dict[str, float | None]:
    """Compare a stock's trailing return to its sector index and NIFTY over
    `lookback` trading days.

    All three series must already be truncated to "today" and share the
    same trading calendar convention (ascending date order, most recent bar
    last). sector_close may be None if no sector index mapping exists for
    the symbol yet.

    Returns:
        stock_return, sector_return, nifty_return: raw trailing returns.
        rs_vs_sector, rs_vs_nifty: stock_return minus the benchmark's return
            (positive = outperforming).
    """
    stock_return = _period_return(stock_close, lookback)
    nifty_return = _period_return(nifty_close, lookback)
    sector_return = _period_return(sector_close, lookback) if sector_close is not None else None

    rs_vs_nifty = (
        stock_return - nifty_return if stock_return is not None and nifty_return is not None else None
    )
    rs_vs_sector = (
        stock_return - sector_return if stock_return is not None and sector_return is not None else None
    )

    return {
        "stock_return": stock_return,
        "sector_return": sector_return,
        "nifty_return": nifty_return,
        "rs_vs_sector": rs_vs_sector,
        "rs_vs_nifty": rs_vs_nifty,
    }
