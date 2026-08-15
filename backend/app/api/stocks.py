"""GET /api/stocks/{symbol}/indicators — see docs/api.md."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.models import Stock, TechnicalIndicator
from app.db.session import get_db
from app.schemas.stocks import IndicatorPoint

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/{symbol}/indicators", response_model=list[IndicatorPoint])
def get_stock_indicators(
    symbol: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(get_db),
) -> list[IndicatorPoint]:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    rows = (
        db.query(TechnicalIndicator)
        .filter(
            TechnicalIndicator.stock_id == stock.id,
            TechnicalIndicator.trade_date >= start_date,
            TechnicalIndicator.trade_date <= end_date,
        )
        .order_by(TechnicalIndicator.trade_date.asc())
        .all()
    )

    return [
        IndicatorPoint(
            date=r.trade_date,
            ema9=r.ema9,
            ema20=r.ema20,
            ema50=r.ema50,
            sma100=r.sma100,
            sma200=r.sma200,
            rsi=r.rsi,
            macd=r.macd,
            macd_signal=r.macd_signal,
            atr=r.atr,
            bb_upper=r.bb_upper,
            bb_lower=r.bb_lower,
        )
        for r in rows
    ]
