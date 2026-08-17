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


def run_news_collection() -> None:
    """Fetches + classifies + persists news (app/news/persist.py) — a
    separate top-level try/except from run_eod_ingestion's own so a news
    source outage never blocks price ingestion. Runs once per EOD cycle
    for now; a genuinely intraday news refresh needs the continuous
    monitoring loop (not built yet, see the news/events discussion in
    docs/engine.md).
    """
    logger.info("News collection job starting")
    from app.news.persist import fetch_classify_and_persist

    db: Session = SessionLocal()
    try:
        inserted = fetch_classify_and_persist(db)
        db.commit()
        logger.info("News collection job complete: %d new articles", inserted)
    except Exception:
        db.rollback()
        logger.exception("News collection job failed")
    finally:
        db.close()


def run_eod_ingestion() -> None:
    """Pulls angel_one_ohlcv for the active universe, then chains into derived
    data refresh."""
    logger.info("EOD ingestion job starting")
    from app.data.angel_one_ohlcv import ingest_symbol
    from app.db.models import Stock

    run_news_collection()

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
BACKTEST_PERSIST_MAX_RETRIES = 3


def _run_backtest_and_persist_with_retry(db: Session, **kwargs):
    """run_backtest_and_persist does all its compute (the full state-machine
    replay) BEFORE any DB writes — for a large universe/window that compute
    phase can run long enough that the already-checked-out connection goes
    idle and gets killed by Neon's serverless proxy before the write phase
    starts. pool_pre_ping (db/session.py) only guards connections at POOL
    CHECKOUT time, not a connection that goes stale mid-use within a single
    already-checked-out session — it can't catch this.

    Observed live 2026-08-17: five separate attempts (two standalone runs
    plus three retries reusing the same broken session) all failed at the
    exact same first INSERT with "SSL connection has been closed
    unexpectedly". Isolating that one row and inserting it via a brand new
    SessionLocal() succeeded immediately — proving the row's data was never
    the problem, the session itself was dead and simply calling
    db.rollback() on a dead session does not reliably resurrect it (despite
    SQLAlchemy's pool_pre_ping and disconnect-invalidation machinery — this
    codebase's actual observed behavior, not the theoretical guarantee).

    Fix: each retry gets a genuinely NEW SessionLocal(), not the original
    (possibly-broken) `db` passed in — only the first attempt uses the
    caller's session. The caller's own `db` remains valid for whatever it
    does before/after this call (e.g. run_breakout_state_advance's NIFTY
    query and the alert-sending step afterward) since those are separate
    reads that see this function's commit regardless of which connection
    performed it.
    """
    from sqlalchemy.exc import OperationalError

    from app.backtest.simulator import run_backtest_and_persist

    # Deliberately NOT `from app.db.session import SessionLocal` here — that
    # would re-fetch the original, unpatched object and bypass tests'
    # monkeypatch.setattr(scheduler_module, "SessionLocal", ...) (see
    # tests/test_scheduler.py's db_session fixture). This module's own
    # top-level `SessionLocal` (imported once, patched by tests) is what
    # must be used, referenced here as the bare module-global name.

    last_error: OperationalError | None = None
    session = db
    opened_fresh_session = False
    try:
        for attempt in range(1, BACKTEST_PERSIST_MAX_RETRIES + 1):
            try:
                result = run_backtest_and_persist(session, **kwargs)
                session.commit()
                return result
            except OperationalError as exc:
                last_error = exc
                try:
                    session.rollback()
                except Exception:
                    pass  # rollback on an already-dead connection can itself raise — ignore, we're discarding this session anyway
                logger.warning(
                    "run_backtest_and_persist attempt %d/%d hit a DB connection error, retrying with a fresh session: %s",
                    attempt, BACKTEST_PERSIST_MAX_RETRIES, exc,
                )
                if opened_fresh_session:
                    session.close()
                session = SessionLocal()
                opened_fresh_session = True
    finally:
        if opened_fresh_session:
            session.close()

    raise last_error


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
    from app.data.angel_one_ohlcv import fetch_daily_ohlcv
    from app.data.yfinance_client import upsert_daily_ohlcv
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
        _run_backtest_and_persist_with_retry(
            db,
            strategy_version=strategy_version,
            start_date=start,
            end_date=end,
            symbols=symbols,
        )
        logger.info(
            "Breakout state advance job complete, %d symbols processed, strategy_version=%s",
            len(symbols), strategy_version,
        )

        _send_alerts_for_todays_confirmed_setups(db, strategy_version=strategy_version, signal_date=end)
    except Exception:
        db.rollback()
        logger.exception("Breakout state advance job failed")
    finally:
        db.close()


def _send_alerts_for_todays_confirmed_setups(db: Session, *, strategy_version: str, signal_date: date) -> None:
    """Alerts only for setups signaled on `signal_date` (today's bar) from
    this run's `strategy_version` — run_breakout_state_advance replays the
    full STATE_ADVANCE_WINDOW_DAYS trailing window fresh every day (see its
    docstring), so without this filter every historical setup in that
    window would re-alert daily. signal_date + strategy_version together
    isolate genuinely new signals.
    """
    from app.breakout.scoring import score_tier
    from app.breakout.states import BreakoutState
    from app.db.models import Stock, TradeSetup
    from app.news.event_caution import build_event_caution_index, event_caution_for_symbol
    from app.notifications.telegram import send_trade_setup_alert

    todays_setups = db.scalars(
        select(TradeSetup).where(
            TradeSetup.strategy_version == strategy_version,
            TradeSetup.signal_date == signal_date,
            TradeSetup.state == BreakoutState.CONFIRMED,
        )
    ).all()

    if not todays_setups:
        return

    try:
        event_index = build_event_caution_index(db)
    except Exception:
        logger.exception("Event-caution news fetch failed — alerts will go out without caution flags")
        event_index = {}

    for setup in todays_setups:
        stock = db.get(Stock, setup.stock_id)
        if stock is None:
            continue
        sent = send_trade_setup_alert(
            symbol=stock.symbol,
            setup_type=setup.setup_type,
            entry_price=float(setup.entry_price) if setup.entry_price is not None else None,
            stop_loss=float(setup.stop_loss),
            target_1r=float(setup.target_1r),
            target_1_5r=float(setup.target_1_5r),
            target_2r=float(setup.target_2r),
            target_3r=float(setup.target_3r),
            nearest_structural_target=float(setup.nearest_structural_target) if setup.nearest_structural_target is not None else None,
            score=float(setup.score),
            tier=score_tier(float(setup.score)),
            event_caution=event_caution_for_symbol(stock.symbol, event_index),
        )
        if sent:
            logger.info("Telegram alert sent for %s (%s, score=%.0f)", stock.symbol, setup.setup_type, float(setup.score))


def run_health_pinger() -> None:
    """Self-pings /health so Render's free-tier web service doesn't spin
    down from 15 min of inbound-traffic idleness. No-op without
    render_external_url set (e.g. local dev)."""
    import requests

    url = settings.render_external_url.rstrip("/") + "/health"
    try:
        response = requests.get(url, timeout=10)
        logger.info("Health pinger: %s -> %d", url, response.status_code)
    except requests.RequestException:
        logger.exception("Health pinger request failed for %s", url)


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

    if settings.render_external_url:
        scheduler.add_job(
            run_health_pinger,
            trigger="interval",
            minutes=settings.health_pinger_interval_minutes,
            id="health_pinger",
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info(
            "Health pinger scheduled: every %d min against %s",
            settings.health_pinger_interval_minutes,
            settings.render_external_url,
        )
    else:
        logger.info("Health pinger disabled — render_external_url not set")

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
