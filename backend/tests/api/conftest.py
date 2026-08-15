"""Shared fixtures for app/api/ route tests.

No live DB — DATABASE_URL in this environment points at a real Neon Postgres
instance, which must never be touched by tests. Each test gets a fresh
in-memory SQLite engine (StaticPool so all connections share the same
:memory: database) with app.db.models' tables created via
Base.metadata.create_all(), wired in via FastAPI's
app.dependency_overrides[get_db] — the same `get_db` function object every
router imports and depends on.

TestClient(app) is constructed WITHOUT the `with` context-manager form, so
FastAPI's lifespan (app/main.py) never runs — that lifespan calls
SessionBase.metadata.create_all(bind=engine) against the real configured
engine and starts the APScheduler, neither of which tests should trigger.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models as models  # noqa: F401  (registers ORM tables on Base.metadata)
from app.db.models import (
    BacktestResult,
    DailyOHLCV,
    Stock,
    TechnicalIndicator,
    TradeOutcome,
    TradeSetup,
)
from app.db.session import Base, get_db
from app.main import app


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Fresh in-memory SQLite session, tables created, wired into the app via
    dependency_overrides. Yields the same Session class tests can use to seed
    rows directly.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db() -> Iterator[Session]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """TestClient wired to the in-memory DB. Deliberately NOT used as a `with`
    block (which would trigger app.main's lifespan — real-DB create_all
    against the live Neon Postgres instance plus scheduler start). Plain
    instantiation skips lifespan entirely while still routing requests.
    """
    return TestClient(app, raise_server_exceptions=False)


def make_stock(
    db: Session,
    symbol: str = "RELIANCE",
    sector: str | None = "Energy",
    listing_status: str = "ACTIVE",
) -> Stock:
    stock = Stock(symbol=symbol, name=symbol.title(), sector=sector, listing_status=listing_status)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def make_daily_bar(
    db: Session,
    stock: Stock,
    trade_date: date,
    close: float = 500.0,
    volume: int = 2_000_000,
) -> DailyOHLCV:
    bar = DailyOHLCV(
        stock_id=stock.id,
        trade_date=trade_date,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        adjusted_close=close,
        volume=volume,
    )
    db.add(bar)
    db.commit()
    db.refresh(bar)
    return bar


def make_indicator(
    db: Session,
    stock: Stock,
    trade_date: date,
    **overrides: float | None,
) -> TechnicalIndicator:
    defaults = dict(
        ema9=100.0,
        ema20=99.0,
        ema50=98.0,
        sma100=97.0,
        sma200=96.0,
        rsi=55.0,
        macd=1.2,
        macd_signal=1.0,
        atr=2.5,
        bb_upper=110.0,
        bb_lower=90.0,
    )
    defaults.update(overrides)
    row = TechnicalIndicator(stock_id=stock.id, trade_date=trade_date, **defaults)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_trade_setup(
    db: Session,
    stock: Stock,
    strategy_version: str = "git-abc123",
    setup_type: str = "BREAKOUT",
    state: str = "CONFIRMED",
    score: float = 72.5,
    score_breakdown: str | None = '{"rvol": 20, "trend": 15}',
    signal_date: date = date(2026, 8, 10),
    entry_date: date | None = date(2026, 8, 11),
    entry_price: float | None = 505.0,
    stop_loss: float = 480.0,
    target_1r: float = 530.0,
    target_1_5r: float = 542.5,
    target_2r: float = 555.0,
    target_3r: float = 580.0,
    nearest_structural_target: float | None = 560.0,
    created_at: datetime | None = None,
) -> TradeSetup:
    setup = TradeSetup(
        stock_id=stock.id,
        setup_type=setup_type,
        strategy_version=strategy_version,
        signal_date=signal_date,
        entry_date=entry_date,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1r=target_1r,
        target_1_5r=target_1_5r,
        target_2r=target_2r,
        target_3r=target_3r,
        nearest_structural_target=nearest_structural_target,
        score=score,
        score_breakdown=score_breakdown,
        state=state,
    )
    if created_at is not None:
        setup.created_at = created_at
    db.add(setup)
    db.commit()
    db.refresh(setup)
    return setup


def make_trade_outcome(
    db: Session,
    setup: TradeSetup,
    mfe: float | None = 3.2,
    mae: float | None = -0.8,
    target_hit: str | None = "2R",
    sl_hit: bool = False,
    holding_days: int | None = 5,
    exit_reason: str | None = "TARGET_HIT",
) -> TradeOutcome:
    outcome = TradeOutcome(
        trade_setup_id=setup.id,
        mfe=mfe,
        mae=mae,
        target_hit=target_hit,
        sl_hit=sl_hit,
        holding_days=holding_days,
        exit_reason=exit_reason,
    )
    db.add(outcome)
    db.commit()
    db.refresh(outcome)
    return outcome


def make_backtest_result(
    db: Session,
    strategy_version: str = "git-abc123",
    run_date: date = date(2026, 8, 1),
    start_date: date = date(2025, 1, 1),
    end_date: date = date(2026, 7, 31),
    total_trades: int = 120,
    win_rate: float | None = 0.55,
    profit_factor: float | None = 1.8,
    avg_mfe: float | None = 3.1,
    avg_mae: float | None = -0.9,
    avg_holding_days: float | None = 6.4,
    breakdown_by_score_bucket: str | None = '{"70-80": 40}',
    breakdown_by_sector: str | None = '{"Energy": 20}',
    breakdown_by_day_of_week: str | None = '{"Monday": 25}',
    breakdown_by_regime: str | None = '{"bull": 80}',
) -> BacktestResult:
    result = BacktestResult(
        strategy_version=strategy_version,
        run_date=run_date,
        start_date=start_date,
        end_date=end_date,
        total_trades=total_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_mfe=avg_mfe,
        avg_mae=avg_mae,
        avg_holding_days=avg_holding_days,
        breakdown_by_score_bucket=breakdown_by_score_bucket,
        breakdown_by_sector=breakdown_by_sector,
        breakdown_by_day_of_week=breakdown_by_day_of_week,
        breakdown_by_regime=breakdown_by_regime,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result
