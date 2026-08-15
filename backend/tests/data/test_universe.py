"""Tests for app.data.universe.get_universe.

Coverage: liquidity filter (price floor, 20D avg turnover floor in crores
INR) and listing_status exclusion, each tested on both sides (pass and
reject), plus as_of_date defaulting to the latest trade_date present.

Uses an isolated in-memory SQLite session (bound to the same declarative
Base as the real models) — no DATABASE_URL, no network, no Postgres.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.data.universe import CRORE, TURNOVER_WINDOW_DAYS, get_universe
from app.db.models import DailyOHLCV, Stock
from app.db.session import Base
from app.config import settings


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_stock(session: Session, symbol: str, listing_status: str = "ACTIVE", sector: str = "IT") -> Stock:
    stock = Stock(symbol=symbol, sector=sector, listing_status=listing_status)
    session.add(stock)
    session.flush()
    return stock


def _add_bars(session: Session, stock: Stock, close: float, volume: int, n: int = TURNOVER_WINDOW_DAYS, end: date = date(2024, 3, 1)) -> None:
    for i in range(n):
        trade_date = end - timedelta(days=n - 1 - i)
        session.add(
            DailyOHLCV(
                stock_id=stock.id,
                trade_date=trade_date,
                open=close,
                high=close,
                low=close,
                close=close,
                adjusted_close=close,
                volume=volume,
            )
        )
    session.flush()


def test_empty_db_returns_empty_list(db_session):
    result = get_universe(db_session)

    assert result == []


def test_symbol_passes_when_price_and_turnover_above_floors(db_session):
    stock = _add_stock(db_session, "TCS")
    # turnover = close * volume / 1e7; pick values clearly above both floors
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "TCS" in symbols


def test_symbol_rejected_when_price_at_or_below_floor(db_session):
    stock = _add_stock(db_session, "PENNY")
    # price exactly at floor (must be excluded: filter uses close <= floor as reject)
    _add_bars(db_session, stock, close=settings.universe_price_floor, volume=10_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "PENNY" not in symbols


def test_symbol_passes_when_price_just_above_floor_and_turnover_sufficient(db_session):
    stock = _add_stock(db_session, "JUSTOK")
    _add_bars(db_session, stock, close=settings.universe_price_floor + 1.0, volume=10_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "JUSTOK" in symbols


def test_symbol_rejected_when_turnover_at_or_below_floor(db_session):
    stock = _add_stock(db_session, "LOWVOL")
    # turnover_cr = close * volume / 1e7. Solve for volume such that
    # turnover exactly equals the floor (rejected, since filter is <=).
    close = 100.0
    volume = int(settings.universe_turnover_floor_cr * CRORE / close)
    _add_bars(db_session, stock, close=close, volume=volume)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "LOWVOL" not in symbols


def test_symbol_passes_when_turnover_just_above_floor(db_session):
    stock = _add_stock(db_session, "OKVOL")
    close = 100.0
    # volume that produces turnover slightly above the floor
    volume = int((settings.universe_turnover_floor_cr * CRORE / close) * 1.5)
    _add_bars(db_session, stock, close=close, volume=volume)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "OKVOL" in symbols


def test_turnover_computed_as_mean_close_times_volume_over_1e7(db_session):
    stock = _add_stock(db_session, "CALC")
    _add_bars(db_session, stock, close=200.0, volume=1_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    entry = next(r for r in result if r["symbol"] == "CALC")
    expected_turnover_cr = (200.0 * 1_000_000) / CRORE
    assert entry["avg_turnover_20d"] == pytest.approx(expected_turnover_cr)


def test_non_active_listing_status_excluded(db_session):
    stock = _add_stock(db_session, "SUSPENDED", listing_status="SUSPENDED")
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "SUSPENDED" not in symbols


def test_active_listing_status_included(db_session):
    stock = _add_stock(db_session, "ACTIVESTOCK", listing_status="ACTIVE")
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "ACTIVESTOCK" in symbols


def test_delisted_status_excluded(db_session):
    stock = _add_stock(db_session, "DELISTED", listing_status="DELISTED")
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "DELISTED" not in symbols


def test_stock_with_no_bars_is_skipped_without_error(db_session):
    _add_stock(db_session, "NOBARS")

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    symbols = [r["symbol"] for r in result]
    assert "NOBARS" not in symbols


def test_as_of_date_defaults_to_latest_trade_date_present(db_session):
    stock = _add_stock(db_session, "DEFAULTED")
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000, end=date(2024, 5, 15))

    result = get_universe(db_session)  # no as_of_date passed

    symbols = [r["symbol"] for r in result]
    assert "DEFAULTED" in symbols


def test_result_shape_matches_documented_fields(db_session):
    stock = _add_stock(db_session, "SHAPE", sector="BANKING")
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000)

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    entry = next(r for r in result if r["symbol"] == "SHAPE")
    assert set(entry.keys()) == {"symbol", "sector", "listing_status", "close", "avg_turnover_20d"}
    assert entry["sector"] == "BANKING"
    assert entry["listing_status"] == "ACTIVE"
    assert entry["close"] == pytest.approx(3000.0)


def test_uses_only_up_to_20_day_window_even_with_more_history(db_session):
    stock = _add_stock(db_session, "LONGHIST")
    # 40 days of history but window should only use the most recent 20
    _add_bars(db_session, stock, close=3000.0, volume=1_000_000, n=40, end=date(2024, 3, 1))

    result = get_universe(db_session, as_of_date=date(2024, 3, 1))

    entry = next(r for r in result if r["symbol"] == "LONGHIST")
    # flat price/volume, so turnover is unaffected by window size here —
    # this test mainly proves no crash/duplication when more than 20 rows exist
    assert entry["avg_turnover_20d"] == pytest.approx((3000.0 * 1_000_000) / CRORE)
