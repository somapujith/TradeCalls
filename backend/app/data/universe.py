"""Liquidity-filtered universe — computed on request, not a stored table.

price > universe_price_floor, 20D avg turnover > universe_turnover_floor_cr
crores INR, excludes illiquid/suspended (listing_status != ACTIVE).
See docs/engine.md#modules, docs/db.md (no universe snapshot table in v1),
and docs/api.md's GET /api/universe for the exact response shape.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import DailyOHLCV, Stock

TURNOVER_WINDOW_DAYS = 20
CRORE = 1e7


def get_universe(session: Session, as_of_date: date | None = None) -> list[dict]:
    """Recompute the liquidity-filtered universe.

    as_of_date: defaults to the latest trade_date present in daily_ohlcv
    (matching docs/api.md GET /api/universe's `as_of_date` default).

    Returns a list of dicts shaped exactly per docs/api.md:
    symbol, sector, listing_status, close, avg_turnover_20d.
    """
    if as_of_date is None:
        as_of_date = session.scalar(select(DailyOHLCV.trade_date).order_by(DailyOHLCV.trade_date.desc()))
        if as_of_date is None:
            return []

    stocks = session.scalars(select(Stock).where(Stock.listing_status == "ACTIVE")).all()

    results: list[dict] = []
    for stock in stocks:
        window_bars = session.scalars(
            select(DailyOHLCV)
            .where(DailyOHLCV.stock_id == stock.id, DailyOHLCV.trade_date <= as_of_date)
            .order_by(DailyOHLCV.trade_date.desc())
            .limit(TURNOVER_WINDOW_DAYS)
        ).all()

        if not window_bars:
            continue

        latest_bar = max(window_bars, key=lambda bar: bar.trade_date)
        close = float(latest_bar.close)
        if close <= settings.universe_price_floor:
            continue

        avg_turnover_cr = sum(float(bar.close) * float(bar.volume) for bar in window_bars) / len(window_bars) / CRORE
        if avg_turnover_cr <= settings.universe_turnover_floor_cr:
            continue

        results.append(
            {
                "symbol": stock.symbol,
                "sector": stock.sector,
                "listing_status": stock.listing_status,
                "close": close,
                "avg_turnover_20d": avg_turnover_cr,
            }
        )

    return results
