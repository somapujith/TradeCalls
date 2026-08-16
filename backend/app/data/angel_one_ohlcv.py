"""Daily OHLCV per symbol via Angel One SmartAPI — replaces yfinance as the
ingestion source (yfinance started failing wholesale, Yahoo blocking/empty
responses, observed 2026-08-17 — see docs/engine.md#live-data-angel-one).

Same output contract as the yfinance_client module it replaces
(fetch_daily_ohlcv -> DataFrame[date, open, high, low, close,
adjusted_close, volume]) so scheduler.py's call sites don't change shape.
upsert_daily_ohlcv is reused unmodified from yfinance_client — that
function is source-agnostic (just a DB upsert keyed on stock_id/trade_date),
despite living in a yfinance-named module.

Known limitation carried over from this swap: Angel One's getCandleData
does NOT return a separate split/dividend-adjusted close the way yfinance's
"Adj Close" did. adjusted_close is set equal to close here. A stock that
splits or pays a large dividend during the backtest window will show a
discontinuity that the old yfinance-adjusted data wouldn't have had — not
handled, not silently correct, flagged here since docs/db.md's design
notes assumed adjusted-close-everywhere and that assumption now only holds
loosely.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.data.angel_one_client import INTERVAL_ONE_DAY, AngelOneRequestError, get_candle_data
from app.data.angel_one_scrip_master import NIFTY_50_INDEX_TOKEN, resolve_equity_token
from app.data.yfinance_client import OHLCV_COLUMNS, upsert_daily_ohlcv  # noqa: F401  (re-exported, source-agnostic)

logger = logging.getLogger(__name__)

NIFTY_SYMBOL = "^NSEI"


def _resolve_token(symbol: str) -> str:
    if symbol == NIFTY_SYMBOL:
        return NIFTY_50_INDEX_TOKEN
    token = resolve_equity_token(symbol)
    if token is None:
        raise AngelOneRequestError(f"No Angel One scrip-master token found for symbol {symbol!r}")
    return token


def fetch_daily_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily OHLCV for one symbol between start and end via Angel One.

    Same output shape as yfinance_client.fetch_daily_ohlcv:
    [date, open, high, low, close, adjusted_close, volume]. Sleeps
    settings.angel_one_candle_request_delay_seconds before returning, so
    callers looping over many symbols don't need their own rate-limit
    handling (see settings.angel_one_candle_request_delay_seconds'
    docstring in config.py for why this delay exists and how sure we are
    of the number).
    """
    token = _resolve_token(symbol)

    candles = get_candle_data(
        token,
        INTERVAL_ONE_DAY,
        datetime(start.year, start.month, start.day),
        datetime(end.year, end.month, end.day),
    )

    time.sleep(settings.angel_one_candle_request_delay_seconds)

    if not candles:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    rows = []
    for candle in candles:
        trade_date = datetime.fromisoformat(candle["timestamp"]).date()
        rows.append(
            {
                "date": trade_date,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "adjusted_close": candle["close"],  # see module docstring: no split-adjustment from Angel One
                "volume": candle["volume"],
            }
        )

    return pd.DataFrame(rows, columns=OHLCV_COLUMNS)


def ingest_symbol(session: Session, symbol: str, start: date, end: date) -> int:
    """Fetch + upsert one symbol's daily bars. Returns rows written.
    Mirrors yfinance_client.ingest_symbol's signature exactly."""
    from app.db.models import Stock
    from sqlalchemy import select

    stock = session.scalar(select(Stock).where(Stock.symbol == symbol))
    if stock is None:
        raise ValueError(f"Stock {symbol!r} not found — seed it before ingesting")

    bars = fetch_daily_ohlcv(symbol, start, end)
    return upsert_daily_ohlcv(session, stock.id, bars)
