"""Daily OHLCV per symbol via yfinance — adjusted close, skip missing days.

No forward-fill: a gap in the trading calendar is a missing row, never a
duplicated prior close (docs/db.md design notes, docs/engine.md#modules).
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import DailyOHLCV, Stock

logger = logging.getLogger(__name__)

NSE_SUFFIX = ".NS"
OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]


def _to_yf_symbol(symbol: str) -> str:
    return symbol if symbol.endswith(NSE_SUFFIX) else f"{symbol}{NSE_SUFFIX}"


def fetch_daily_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch daily OHLCV for one symbol between start and end (inclusive of start,
    exclusive of end per yfinance convention).

    Retries with backoff using settings.yfinance_max_retries /
    settings.yfinance_backoff_seconds. Returns a DataFrame with columns
    [date, open, high, low, close, adjusted_close, volume], one row per
    trading day actually present — missing days are simply absent, never
    forward-filled.
    """
    yf_symbol = _to_yf_symbol(symbol)
    last_error: Exception | None = None
    raw: pd.DataFrame | None = None

    for attempt in range(1, settings.yfinance_max_retries + 1):
        try:
            raw = yf.download(
                yf_symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            last_error = None
            break
        except Exception as exc:  # yfinance raises assorted network/HTTP errors
            last_error = exc
            logger.warning(
                "yfinance fetch failed for %s (attempt %d/%d): %s",
                symbol,
                attempt,
                settings.yfinance_max_retries,
                exc,
            )
            if attempt < settings.yfinance_max_retries:
                time.sleep(settings.yfinance_backoff_seconds * attempt)

    if last_error is not None:
        raise RuntimeError(
            f"yfinance fetch failed for {symbol} after {settings.yfinance_max_retries} attempts"
        ) from last_error

    if raw is None or raw.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close", "Volume"]
    clean = raw.dropna(subset=required)  # skip missing/partial days rather than forward-fill

    rows: list[dict] = []
    for idx, row in clean.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        adj_close = float(row["Adj Close"]) if "Adj Close" in row and pd.notna(row["Adj Close"]) else float(row["Close"])
        rows.append(
            {
                "date": trade_date,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "adjusted_close": adj_close,
                "volume": int(row["Volume"]),
            }
        )

    return pd.DataFrame(rows, columns=OHLCV_COLUMNS)


def upsert_daily_ohlcv(session: Session, stock_id: int, bars: pd.DataFrame) -> int:
    """Upsert rows from fetch_daily_ohlcv's output into daily_ohlcv, unique on
    (stock_id, trade_date). Returns the number of rows written (inserted or
    updated). Caller owns the transaction (commit/rollback).
    """
    if bars.empty:
        return 0

    existing = {
        row.trade_date: row
        for row in session.scalars(
            select(DailyOHLCV).where(DailyOHLCV.stock_id == stock_id)
        )
    }

    written = 0
    for record in bars.to_dict(orient="records"):
        trade_date = record["date"]
        existing_row = existing.get(trade_date)
        if existing_row is not None:
            existing_row.open = record["open"]
            existing_row.high = record["high"]
            existing_row.low = record["low"]
            existing_row.close = record["close"]
            existing_row.adjusted_close = record["adjusted_close"]
            existing_row.volume = record["volume"]
        else:
            session.add(
                DailyOHLCV(
                    stock_id=stock_id,
                    trade_date=trade_date,
                    open=record["open"],
                    high=record["high"],
                    low=record["low"],
                    close=record["close"],
                    adjusted_close=record["adjusted_close"],
                    volume=record["volume"],
                )
            )
        written += 1

    return written


def ingest_symbol(session: Session, symbol: str, start: date, end: date) -> int:
    """Fetch and upsert daily bars for one symbol already present in `stocks`.
    Raises ValueError if the symbol isn't found in the stocks table.
    """
    stock = session.scalar(select(Stock).where(Stock.symbol == symbol))
    if stock is None:
        raise ValueError(f"Unknown symbol {symbol!r} — not present in stocks table")

    bars = fetch_daily_ohlcv(symbol, start, end)
    return upsert_daily_ohlcv(session, stock.id, bars)
