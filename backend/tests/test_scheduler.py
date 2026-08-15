"""Tests for app.scheduler's EOD job chain (run_eod_ingestion ->
run_derived_data_refresh -> run_breakout_state_advance) — see docs/backend.md's
Scheduler section and module docstring.

This is the production EOD pipeline, so unlike app/news/ (out-of-scope v2
code, excluded from coverage via .coveragerc) it deserves real test
coverage: each job function opens its own SessionLocal() session, so these
tests monkeypatch app.scheduler.SessionLocal to hand back a real in-memory
SQLite session (same convention as tests/api/conftest.py's db_session
fixture) rather than hitting the live Postgres DATABASE_URL, and monkeypatch
the network/compute-heavy collaborators (yfinance, indicator computation,
the backtest simulator) so no real I/O happens. Each job's own internal
per-symbol try/except means a single symbol's failure must not crash the
whole job or block it from committing/chaining — that's exercised
explicitly below, not just the happy path.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.scheduler as scheduler_module
from app.db.models import DailyOHLCV, Stock, TechnicalIndicator
from app.db.session import Base


@pytest.fixture()
def db_session(monkeypatch: pytest.MonkeyPatch):
    """Fresh in-memory SQLite session wired in as app.scheduler.SessionLocal
    (a callable returning a new Session per call, matching the real
    sessionmaker's usage — SessionLocal() opens one, .close() releases it).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(scheduler_module, "SessionLocal", TestSessionLocal)

    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_stock(db_session, symbol: str, listing_status: str = "ACTIVE") -> Stock:
    stock = Stock(symbol=symbol, name=symbol, sector="IT", listing_status=listing_status)
    db_session.add(stock)
    db_session.commit()
    db_session.refresh(stock)
    return stock


def _make_daily_bars(db_session, stock: Stock, n: int = 30, start: date = date(2026, 1, 1)) -> None:
    price = 100.0
    current = start
    for _ in range(n):
        price += 0.5
        db_session.add(
            DailyOHLCV(
                stock_id=stock.id,
                trade_date=current,
                open=price - 0.2,
                high=price + 0.3,
                low=price - 0.4,
                close=price,
                adjusted_close=price,
                volume=100_000,
            )
        )
        current += timedelta(days=1)
    db_session.commit()


# --- run_eod_ingestion -------------------------------------------------


class TestRunEodIngestion:
    def test_calls_ingest_symbol_for_every_active_stock_and_commits(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")
        _make_stock(db_session, "BBB")
        _make_stock(db_session, "CCC", listing_status="DELISTED")  # must be excluded

        ingested_symbols: list[str] = []

        def fake_ingest_symbol(session, symbol, start, end):
            ingested_symbols.append(symbol)
            return 1

        monkeypatch.setattr("app.data.yfinance_client.ingest_symbol", fake_ingest_symbol)

        chained: list[str] = []
        monkeypatch.setattr(
            scheduler_module, "run_derived_data_refresh", lambda: chained.append("derived_data_refresh")
        )

        scheduler_module.run_eod_ingestion()

        assert sorted(ingested_symbols) == ["AAA", "BBB"]
        assert chained == ["derived_data_refresh"]

    def test_per_symbol_exception_is_swallowed_and_other_symbols_still_ingested(self, db_session, monkeypatch):
        """A single symbol's ingest failure must not abort the whole job —
        each symbol has its own try/except/rollback, per module docstring."""
        _make_stock(db_session, "AAA")
        _make_stock(db_session, "BBB")

        ingested_symbols: list[str] = []

        def fake_ingest_symbol(session, symbol, start, end):
            if symbol == "AAA":
                raise RuntimeError("yfinance network error")
            ingested_symbols.append(symbol)
            return 1

        monkeypatch.setattr("app.data.yfinance_client.ingest_symbol", fake_ingest_symbol)
        monkeypatch.setattr(scheduler_module, "run_derived_data_refresh", lambda: None)

        # Must not raise.
        scheduler_module.run_eod_ingestion()

        assert ingested_symbols == ["BBB"]

    def test_chains_into_derived_data_refresh_on_success(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")
        monkeypatch.setattr("app.data.yfinance_client.ingest_symbol", lambda session, symbol, start, end: 1)

        call_count = {"n": 0}
        monkeypatch.setattr(scheduler_module, "run_derived_data_refresh", lambda: call_count.__setitem__("n", call_count["n"] + 1))

        scheduler_module.run_eod_ingestion()

        assert call_count["n"] == 1

    def test_no_active_stocks_still_chains_without_crashing(self, db_session, monkeypatch):
        chained: list[str] = []
        monkeypatch.setattr(scheduler_module, "run_derived_data_refresh", lambda: chained.append("x"))

        scheduler_module.run_eod_ingestion()

        assert chained == ["x"]

    def test_unexpected_top_level_exception_rolls_back_and_does_not_chain(self, db_session, monkeypatch):
        """If the stocks query itself blows up (not a per-symbol failure),
        the job must roll back, log, and return WITHOUT chaining into
        run_derived_data_refresh — chaining on a fundamentally broken job
        run would just cascade the failure downstream.
        """

        chained: list[str] = []
        monkeypatch.setattr(scheduler_module, "run_derived_data_refresh", lambda: chained.append("x"))

        # Force the failure via select(...) itself raising, by monkeypatching
        # the imported `select` inside scheduler.run_eod_ingestion's module
        # namespace (module-level `from sqlalchemy import select`).
        def _select_boom(*args, **kwargs):
            raise RuntimeError("query construction failed")

        monkeypatch.setattr(scheduler_module, "select", _select_boom)

        scheduler_module.run_eod_ingestion()  # must not raise

        assert chained == []


# --- run_derived_data_refresh -------------------------------------------


class TestRunDerivedDataRefresh:
    def test_computes_and_persists_indicators_then_chains(self, db_session, monkeypatch):
        stock = _make_stock(db_session, "AAA")
        _make_daily_bars(db_session, stock, n=30)

        chained: list[str] = []
        monkeypatch.setattr(scheduler_module, "run_breakout_state_advance", lambda: chained.append("x"))

        scheduler_module.run_derived_data_refresh()

        assert chained == ["x"]
        rows = db_session.query(TechnicalIndicator).filter(TechnicalIndicator.stock_id == stock.id).all()
        assert len(rows) == 1  # one row for the latest trade_date only

    def test_updates_existing_indicator_row_instead_of_duplicating(self, db_session, monkeypatch):
        stock = _make_stock(db_session, "AAA")
        _make_daily_bars(db_session, stock, n=30)
        latest_date = date(2026, 1, 1) + timedelta(days=29)

        db_session.add(TechnicalIndicator(stock_id=stock.id, trade_date=latest_date, ema9=1.0))
        db_session.commit()

        monkeypatch.setattr(scheduler_module, "run_breakout_state_advance", lambda: None)

        scheduler_module.run_derived_data_refresh()

        rows = db_session.query(TechnicalIndicator).filter(TechnicalIndicator.stock_id == stock.id).all()
        assert len(rows) == 1

    def test_symbol_with_no_bars_is_skipped_without_error(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")  # no daily bars at all

        chained: list[str] = []
        monkeypatch.setattr(scheduler_module, "run_breakout_state_advance", lambda: chained.append("x"))

        scheduler_module.run_derived_data_refresh()  # must not raise

        assert chained == ["x"]

    def test_per_symbol_exception_is_swallowed_and_other_symbols_still_refreshed(self, db_session, monkeypatch):
        from app.market.indicators import compute_indicators as real_compute_indicators

        good_stock = _make_stock(db_session, "GOOD")
        _make_daily_bars(db_session, good_stock, n=30)
        bad_stock = _make_stock(db_session, "BAD")
        _make_daily_bars(db_session, bad_stock, n=30)

        calls = {"n": 0}

        def flaky_compute_indicators(bars):
            # Fail on the first call (whichever symbol that happens to be —
            # dict/query iteration order isn't a contract worth depending
            # on) to simulate a transient per-symbol failure, then delegate
            # to the real implementation for every subsequent call.
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("indicator computation blew up")
            return real_compute_indicators(bars)

        monkeypatch.setattr("app.market.indicators.compute_indicators", flaky_compute_indicators)
        monkeypatch.setattr(scheduler_module, "run_breakout_state_advance", lambda: None)

        scheduler_module.run_derived_data_refresh()  # must not raise

        total_rows = db_session.query(TechnicalIndicator).count()
        assert total_rows == 1  # exactly one of the two symbols got persisted

    def test_unexpected_top_level_exception_rolls_back_and_does_not_chain(self, db_session, monkeypatch):
        """The outer stocks query (before the per-symbol loop even starts)
        failing must roll back and return without chaining — patched via a
        SessionLocal that hands back a session whose .scalars() itself
        raises, since run_derived_data_refresh imports `select` fresh from
        sqlalchemy on every call (module-level scheduler.select isn't what
        it actually resolves against).
        """
        chained: list[str] = []
        monkeypatch.setattr(scheduler_module, "run_breakout_state_advance", lambda: chained.append("x"))

        real_session = scheduler_module.SessionLocal()

        def _boom(*args, **kwargs):
            raise RuntimeError("query construction failed")

        monkeypatch.setattr(real_session, "scalars", _boom)
        monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: real_session)

        scheduler_module.run_derived_data_refresh()  # must not raise

        assert chained == []


# --- run_breakout_state_advance -----------------------------------------


class TestRunBreakoutStateAdvance:
    def _fake_nifty_bars(self, n: int = 30) -> pd.DataFrame:
        idx = pd.date_range(date(2026, 1, 1), periods=n, freq="D")
        return pd.DataFrame(
            {"open": 20000.0, "high": 20050.0, "low": 19950.0, "close": 20000.0, "adjusted_close": 20000.0, "volume": 1_000_000},
            index=idx,
        )

    def test_happy_path_calls_downstream_in_order_and_commits(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")

        calls: list[str] = []

        monkeypatch.setattr(
            "app.data.yfinance_client.fetch_daily_ohlcv",
            lambda symbol, start, end: (calls.append("fetch_daily_ohlcv"), self._fake_nifty_bars())[1],
        )
        monkeypatch.setattr(
            "app.data.yfinance_client.upsert_daily_ohlcv",
            lambda session, stock_id, bars: (calls.append("upsert_daily_ohlcv"), len(bars))[1],
        )
        monkeypatch.setattr(
            "app.api.deps.generate_strategy_version", lambda: (calls.append("generate_strategy_version"), "ts-test")[1]
        )
        monkeypatch.setattr(
            "app.backtest.simulator.run_backtest_and_persist",
            lambda session, **kwargs: (calls.append("run_backtest_and_persist"), pd.DataFrame())[1],
        )

        scheduler_module.run_breakout_state_advance()

        assert calls == [
            "fetch_daily_ohlcv",
            "upsert_daily_ohlcv",
            "generate_strategy_version",
            "run_backtest_and_persist",
        ]

        nifty_stock = db_session.query(Stock).filter(Stock.symbol == scheduler_module.NIFTY_SYMBOL).one_or_none()
        assert nifty_stock is not None

    def test_creates_nifty_stock_row_if_missing(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")

        monkeypatch.setattr("app.data.yfinance_client.fetch_daily_ohlcv", lambda symbol, start, end: self._fake_nifty_bars())
        monkeypatch.setattr("app.data.yfinance_client.upsert_daily_ohlcv", lambda session, stock_id, bars: len(bars))
        monkeypatch.setattr("app.api.deps.generate_strategy_version", lambda: "ts-test")
        monkeypatch.setattr("app.backtest.simulator.run_backtest_and_persist", lambda session, **kwargs: pd.DataFrame())

        assert db_session.query(Stock).filter(Stock.symbol == scheduler_module.NIFTY_SYMBOL).one_or_none() is None

        scheduler_module.run_breakout_state_advance()

        nifty_stock = db_session.query(Stock).filter(Stock.symbol == scheduler_module.NIFTY_SYMBOL).one_or_none()
        assert nifty_stock is not None
        assert nifty_stock.listing_status == "INDEX"

    def test_empty_nifty_bars_skips_backtest_without_crashing(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")

        monkeypatch.setattr("app.data.yfinance_client.fetch_daily_ohlcv", lambda symbol, start, end: pd.DataFrame())
        monkeypatch.setattr("app.data.yfinance_client.upsert_daily_ohlcv", lambda session, stock_id, bars: 0)

        called = {"n": 0}
        monkeypatch.setattr(
            "app.backtest.simulator.run_backtest_and_persist",
            lambda session, **kwargs: called.__setitem__("n", called["n"] + 1),
        )

        scheduler_module.run_breakout_state_advance()  # must not raise

        assert called["n"] == 0

    def test_no_active_symbols_skips_backtest_without_crashing(self, db_session, monkeypatch):
        monkeypatch.setattr("app.data.yfinance_client.fetch_daily_ohlcv", lambda symbol, start, end: self._fake_nifty_bars())
        monkeypatch.setattr("app.data.yfinance_client.upsert_daily_ohlcv", lambda session, stock_id, bars: len(bars))

        called = {"n": 0}
        monkeypatch.setattr(
            "app.backtest.simulator.run_backtest_and_persist",
            lambda session, **kwargs: called.__setitem__("n", called["n"] + 1),
        )

        scheduler_module.run_breakout_state_advance()  # must not raise

        assert called["n"] == 0

    def test_downstream_exception_is_caught_rolled_back_and_does_not_crash(self, db_session, monkeypatch):
        _make_stock(db_session, "AAA")

        monkeypatch.setattr("app.data.yfinance_client.fetch_daily_ohlcv", lambda symbol, start, end: self._fake_nifty_bars())
        monkeypatch.setattr("app.data.yfinance_client.upsert_daily_ohlcv", lambda session, stock_id, bars: len(bars))
        monkeypatch.setattr("app.api.deps.generate_strategy_version", lambda: "ts-test")

        def _boom(session, **kwargs):
            raise RuntimeError("simulator blew up")

        monkeypatch.setattr("app.backtest.simulator.run_backtest_and_persist", _boom)

        scheduler_module.run_breakout_state_advance()  # must not raise
