"""APScheduler EOD job chain — see docs/backend.md's Scheduler section.

Three jobs run in sequence, all triggered once daily at
settings.eod_ingestion_hour:eod_ingestion_minute in settings.scheduler_timezone:
ingestion -> derived data refresh -> breakout state advance. The backtest
run is deliberately NOT scheduled here — it's only triggered via the
POST /api/backtest-runs endpoint, per docs/backend.md ("not part of the
daily production path in v1").
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)

INGESTION_LOOKBACK_DAYS = 400  # enough history for SMA200/52W levels on a fresh symbol


def _numeric_or_none(value: float | None) -> float | None:
    """compute_indicators' rolling-window columns are NaN (or pandas' <NA>
    for nullable-dtype columns like rsi) until their window warms up
    (correct, not a bug — see app/market/indicators.py) — a SQLAlchemy
    Numeric column needs None, not NaN/<NA>, so coerce here rather than
    writing either into a numeric column.
    """
    if value is None:
        return None
    import pandas as pd  # deferred: matches this module's lazy-import convention

    if pd.isna(value):
        return None
    return float(value)


def run_eod_ingestion() -> None:
    """Pulls yfinance_client for the active universe, then chains into derived
    data refresh."""
    logger.info("EOD ingestion job starting")
    from app.data.yfinance_client import ingest_symbol
    from app.db.models import Stock

    db: Session = SessionLocal()
    ingested = 0
    try:
        stocks = db.scalars(select(Stock).where(Stock.listing_status == "ACTIVE")).all()
        end = date.today()
        start = end - timedelta(days=INGESTION_LOOKBACK_DAYS)

        for stock in stocks:
            try:
                ingest_symbol(db, stock.symbol, start, end)
                db.commit()
                ingested += 1
            except Exception:
                db.rollback()
                logger.exception("EOD ingestion failed for symbol %s", stock.symbol)

        logger.info("EOD ingestion job complete for %d/%d symbols", ingested, len(stocks))
    except Exception:
        db.rollback()
        logger.exception("EOD ingestion job failed")
        return
    finally:
        db.close()

    run_derived_data_refresh()


def run_derived_data_refresh() -> None:
    """Recomputes indicators/levels for the new day per symbol, persists them,
    then chains into the breakout state advance."""
    logger.info("Derived data refresh job starting")
    import pandas as pd
    from sqlalchemy import select as sa_select

    from app.db.models import DailyOHLCV, Stock, TechnicalIndicator
    from app.market.indicators import compute_indicators

    db: Session = SessionLocal()
    refreshed = 0
    try:
        stocks = db.scalars(sa_select(Stock).where(Stock.listing_status == "ACTIVE")).all()
        today = date.today()

        for stock in stocks:
            try:
                rows = db.scalars(
                    sa_select(DailyOHLCV)
                    .where(DailyOHLCV.stock_id == stock.id)
                    .order_by(DailyOHLCV.trade_date.asc())
                ).all()
                if not rows:
                    continue

                bars = pd.DataFrame(
                    [{"open": float(r.open), "high": float(r.high), "low": float(r.low),
                      "close": float(r.close), "volume": int(r.volume)} for r in rows],
                    index=[r.trade_date for r in rows],
                )
                latest_date = rows[-1].trade_date
                indicators = compute_indicators(bars)
                if indicators.empty:
                    continue
                row = indicators.iloc[-1]

                existing = db.scalar(
                    sa_select(TechnicalIndicator).where(
                        TechnicalIndicator.stock_id == stock.id,
                        TechnicalIndicator.trade_date == latest_date,
                    )
                )
                if existing is None:
                    existing = TechnicalIndicator(stock_id=stock.id, trade_date=latest_date)
                    db.add(existing)

                existing.ema9 = _numeric_or_none(row.ema9)
                existing.ema20 = _numeric_or_none(row.ema20)
                existing.ema50 = _numeric_or_none(row.ema50)
                existing.sma100 = _numeric_or_none(row.sma100)
                existing.sma200 = _numeric_or_none(row.sma200)
                existing.rsi = _numeric_or_none(row.rsi)
                existing.macd = _numeric_or_none(row.macd)
                existing.macd_signal = _numeric_or_none(row.macd_signal)
                existing.atr = _numeric_or_none(row.atr)
                existing.bb_upper = _numeric_or_none(row.bb_upper)
                existing.bb_lower = _numeric_or_none(row.bb_lower)

                db.commit()
                refreshed += 1
            except Exception:
                db.rollback()
                logger.exception("Derived data refresh failed for symbol %s", stock.symbol)

        logger.info("Derived data refresh job complete for %d symbols", refreshed)
    except Exception:
        db.rollback()
        logger.exception("Derived data refresh job failed")
        return
    finally:
        db.close()

    run_breakout_state_advance()


STATE_ADVANCE_WINDOW_DAYS = 120  # enough trailing history for EMA50/retest-window bars_since_confirmed to be meaningful
NIFTY_SYMBOL = "^NSEI"


def run_breakout_state_advance() -> None:
    """Advances each symbol's breakout/dip-buy state on the new bar, scores
    CONFIRMED signals, and persists new trade_setups rows.

    run_backtest's symbol_states always start fresh (WATCH/UPTREND_CONFIRMED)
    per call rather than resuming from breakout_candidates, so "advance one
    day" is approximated here as "replay the trailing window fresh" — each
    run is a complete, self-contained pass tagged with its own
    strategy_version, consistent with how trade_setups/backtest_results
    already work. Costs some redundant recomputation of already-settled
    states each day; acceptable for v1's daily-batch cadence.
    """
    logger.info("Breakout state advance job starting")
    from app.api.deps import generate_strategy_version
    from app.backtest.simulator import run_backtest_and_persist
    from app.data.yfinance_client import fetch_daily_ohlcv, upsert_daily_ohlcv
    from app.db.models import Stock

    db: Session = SessionLocal()
    try:
        end = date.today()
        start = end - timedelta(days=STATE_ADVANCE_WINDOW_DAYS)

        nifty_stock = db.scalar(select(Stock).where(Stock.symbol == NIFTY_SYMBOL))
        if nifty_stock is None:
            nifty_stock = Stock(symbol=NIFTY_SYMBOL, name="NIFTY 50", listing_status="INDEX")
            db.add(nifty_stock)
            db.flush()

        nifty_bars = fetch_daily_ohlcv(NIFTY_SYMBOL, start, end)
        upsert_daily_ohlcv(db, nifty_stock.id, nifty_bars)
        db.commit()

        if nifty_bars.empty:
            logger.error("Breakout state advance skipped — no NIFTY data available for market regime")
            return

        stocks = db.scalars(select(Stock).where(Stock.listing_status == "ACTIVE")).all()
        symbols = [s.symbol for s in stocks]
        if not symbols:
            logger.warning("Breakout state advance skipped — no active symbols in universe")
            return

        strategy_version = generate_strategy_version()
        run_backtest_and_persist(
            db,
            strategy_version=strategy_version,
            start_date=start,
            end_date=end,
            symbols=symbols,
        )
        db.commit()
        logger.info(
            "Breakout state advance job complete, %d symbols processed, strategy_version=%s",
            len(symbols), strategy_version,
        )
    except Exception:
        db.rollback()
        logger.exception("Breakout state advance job failed")
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler.add_job(
        run_eod_ingestion,
        trigger="cron",
        hour=settings.eod_ingestion_hour,
        minute=settings.eod_ingestion_minute,
        id="eod_job_chain",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: EOD job chain at %02d:%02d %s",
        settings.eod_ingestion_hour,
        settings.eod_ingestion_minute,
        settings.scheduler_timezone,
    )
    return scheduler


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
