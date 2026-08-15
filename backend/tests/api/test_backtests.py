"""Tests for GET /api/backtests, GET /api/backtests/{strategy_version}/metrics,
GET /api/backtests/{strategy_version}/trades — see docs/api.md.

Key contract points under test:
- strategy_version is always a required path param on the two per-version
  endpoints (never defaults to "latest").
- GET /api/backtests lists one row per distinct strategy_version (latest
  run_date per version).
- GET /api/backtests/{strategy_version}/metrics 404s if no backtest_results
  row exists for that version.
- GET /api/backtests/{strategy_version}/trades 404s the same way, and
  supports ?symbol=, ?score_min=, ?setup_type= filters.
- A trade_setup without a matching trade_outcomes row must serialize
  outcome: null (outer join, not inner).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import (
    make_backtest_result,
    make_stock,
    make_trade_outcome,
    make_trade_setup,
)


# ---------------------------------------------------------------------------
# GET /api/backtests
# ---------------------------------------------------------------------------


def test_list_backtests_empty_db_returns_empty_list(client: TestClient) -> None:
    resp = client.get("/api/backtests")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_backtests_returns_one_row_per_distinct_strategy_version(
    client: TestClient, db_session: Session
) -> None:
    make_backtest_result(db_session, strategy_version="v1", run_date=date(2026, 1, 1), total_trades=10)
    make_backtest_result(db_session, strategy_version="v2", run_date=date(2026, 2, 1), total_trades=20)

    resp = client.get("/api/backtests")

    assert resp.status_code == 200
    body = resp.json()
    versions = {row["strategy_version"] for row in body}
    assert versions == {"v1", "v2"}
    assert len(body) == 2


def test_list_backtests_uses_latest_run_date_per_version(client: TestClient, db_session: Session) -> None:
    """Two backtest_results rows for the same strategy_version (e.g. a re-run)
    should collapse to a single summary row using the latest run_date."""
    make_backtest_result(
        db_session, strategy_version="v1", run_date=date(2026, 1, 1), total_trades=10, win_rate=0.4
    )
    make_backtest_result(
        db_session, strategy_version="v1", run_date=date(2026, 6, 1), total_trades=99, win_rate=0.6
    )

    resp = client.get("/api/backtests")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["run_date"] == "2026-06-01"
    assert body[0]["total_trades"] == 99


def test_list_backtests_response_shape(client: TestClient, db_session: Session) -> None:
    make_backtest_result(
        db_session,
        strategy_version="v1",
        run_date=date(2026, 1, 1),
        total_trades=42,
        win_rate=0.55,
        profit_factor=1.9,
    )

    resp = client.get("/api/backtests")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert set(row.keys()) == {"strategy_version", "run_date", "total_trades", "win_rate", "profit_factor"}


def test_list_backtests_nullable_win_rate_and_profit_factor(client: TestClient, db_session: Session) -> None:
    make_backtest_result(
        db_session, strategy_version="v1", run_date=date(2026, 1, 1), win_rate=None, profit_factor=None
    )

    resp = client.get("/api/backtests")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["win_rate"] is None
    assert row["profit_factor"] is None


# ---------------------------------------------------------------------------
# GET /api/backtests/{strategy_version}/metrics
# ---------------------------------------------------------------------------


def test_get_metrics_404_when_strategy_version_unknown(client: TestClient) -> None:
    resp = client.get("/api/backtests/does-not-exist/metrics")

    assert resp.status_code == 404
    assert "detail" in resp.json()
    assert isinstance(resp.json()["detail"], str)


def test_get_metrics_happy_path(client: TestClient, db_session: Session) -> None:
    make_backtest_result(
        db_session,
        strategy_version="v1",
        total_trades=100,
        win_rate=0.6,
        profit_factor=2.1,
        avg_mfe=3.5,
        avg_mae=-1.1,
        avg_holding_days=7.2,
        breakdown_by_score_bucket='{"70-80": 30}',
        breakdown_by_sector='{"IT": 25}',
        breakdown_by_day_of_week='{"Monday": 20}',
        breakdown_by_regime='{"bull": 60}',
    )

    resp = client.get("/api/backtests/v1/metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_version"] == "v1"
    assert body["total_trades"] == 100
    assert body["win_rate"] == 0.6
    assert body["profit_factor"] == 2.1
    assert body["avg_mfe"] == 3.5
    assert body["avg_mae"] == -1.1
    assert body["avg_holding_days"] == 7.2
    assert body["breakdown_by_score_bucket"] == {"70-80": 30}
    assert body["breakdown_by_sector"] == {"IT": 25}
    assert body["breakdown_by_day_of_week"] == {"Monday": 20}
    assert body["breakdown_by_regime"] == {"bull": 60}


def test_get_metrics_uses_latest_run_date_when_multiple_rows(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1", run_date=date(2026, 1, 1), total_trades=10)
    make_backtest_result(db_session, strategy_version="v1", run_date=date(2026, 5, 1), total_trades=50)

    resp = client.get("/api/backtests/v1/metrics")

    assert resp.status_code == 200
    assert resp.json()["total_trades"] == 50


def test_get_metrics_breakdown_null_when_column_is_null(client: TestClient, db_session: Session) -> None:
    make_backtest_result(
        db_session,
        strategy_version="v1",
        breakdown_by_score_bucket=None,
        breakdown_by_sector=None,
        breakdown_by_day_of_week=None,
        breakdown_by_regime=None,
    )

    resp = client.get("/api/backtests/v1/metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["breakdown_by_score_bucket"] is None
    assert body["breakdown_by_sector"] is None
    assert body["breakdown_by_day_of_week"] is None
    assert body["breakdown_by_regime"] is None


def test_get_metrics_strategy_version_is_case_sensitive_path_param(
    client: TestClient, db_session: Session
) -> None:
    """strategy_version is an opaque string key (git-<hash> or ts-<timestamp>)
    — no case-folding should be applied, unlike ?symbol= elsewhere."""
    make_backtest_result(db_session, strategy_version="git-abc123")

    resp = client.get("/api/backtests/GIT-ABC123/metrics")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/backtests/{strategy_version}/trades
# ---------------------------------------------------------------------------


def test_get_trades_404_when_strategy_version_unknown(client: TestClient) -> None:
    resp = client.get("/api/backtests/does-not-exist/trades")

    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)


def test_get_trades_happy_path_with_outcome(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="RELIANCE")
    setup = make_trade_setup(db_session, stock, strategy_version="v1", score=75.0)
    make_trade_outcome(db_session, setup, mfe=4.0, mae=-1.0, target_hit="2R", holding_days=6)

    resp = client.get("/api/backtests/v1/trades")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["symbol"] == "RELIANCE"
    assert row["setup_type"] == "BREAKOUT"
    assert row["score"] == 75.0
    assert row["targets"] == [530.0, 542.5, 555.0, 580.0, 560.0]
    assert row["outcome"] is not None
    assert row["outcome"]["mfe"] == 4.0
    assert row["outcome"]["target_hit"] == "2R"
    assert row["outcome"]["holding_days"] == 6


def test_get_trades_outcome_null_when_setup_not_closed(client: TestClient, db_session: Session) -> None:
    """A trade_setup with no matching trade_outcomes row (not yet closed
    within the backtest window) must serialize outcome: null, not error or
    drop the row."""
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="TCS")
    make_trade_setup(db_session, stock, strategy_version="v1")

    resp = client.get("/api/backtests/v1/trades")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["outcome"] is None


def test_get_trades_filters_by_symbol(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock_a = make_stock(db_session, symbol="RELIANCE")
    stock_b = make_stock(db_session, symbol="TCS")
    make_trade_setup(db_session, stock_a, strategy_version="v1")
    make_trade_setup(db_session, stock_b, strategy_version="v1")

    resp = client.get("/api/backtests/v1/trades", params={"symbol": "TCS"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "TCS"


def test_get_trades_filter_by_symbol_is_case_insensitive(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="TCS")
    make_trade_setup(db_session, stock, strategy_version="v1")

    resp = client.get("/api/backtests/v1/trades", params={"symbol": "tcs"})

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_trades_filters_by_score_min(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock_a = make_stock(db_session, symbol="AAA")
    stock_b = make_stock(db_session, symbol="BBB")
    make_trade_setup(db_session, stock_a, strategy_version="v1", score=40.0)
    make_trade_setup(db_session, stock_b, strategy_version="v1", score=90.0)

    resp = client.get("/api/backtests/v1/trades", params={"score_min": 60})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "BBB"


def test_get_trades_filters_by_setup_type(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock_a = make_stock(db_session, symbol="AAA")
    stock_b = make_stock(db_session, symbol="BBB")
    make_trade_setup(db_session, stock_a, strategy_version="v1", setup_type="BREAKOUT")
    make_trade_setup(db_session, stock_b, strategy_version="v1", setup_type="DIP_BUY")

    resp = client.get("/api/backtests/v1/trades", params={"setup_type": "DIP_BUY"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["setup_type"] == "DIP_BUY"


def test_get_trades_filter_by_setup_type_is_case_insensitive(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="AAA")
    make_trade_setup(db_session, stock, strategy_version="v1", setup_type="DIP_BUY")

    resp = client.get("/api/backtests/v1/trades", params={"setup_type": "dip_buy"})

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_trades_combines_multiple_filters(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock_a = make_stock(db_session, symbol="AAA")
    stock_b = make_stock(db_session, symbol="BBB")
    make_trade_setup(db_session, stock_a, strategy_version="v1", setup_type="BREAKOUT", score=80.0)
    make_trade_setup(db_session, stock_b, strategy_version="v1", setup_type="DIP_BUY", score=80.0)

    resp = client.get(
        "/api/backtests/v1/trades",
        params={"setup_type": "BREAKOUT", "score_min": 50},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "AAA"


def test_get_trades_does_not_leak_other_strategy_versions(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    make_backtest_result(db_session, strategy_version="v2")
    stock = make_stock(db_session, symbol="RELIANCE")
    make_trade_setup(db_session, stock, strategy_version="v1")
    make_trade_setup(db_session, stock, strategy_version="v2")

    resp = client.get("/api/backtests/v1/trades")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_trades_pagination_limit(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="RELIANCE")
    for i in range(5):
        make_trade_setup(db_session, stock, strategy_version="v1", signal_date=date(2026, 1, 1 + i))

    resp = client.get("/api/backtests/v1/trades", params={"limit": 2})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_trades_pagination_offset(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="RELIANCE")
    for i in range(5):
        make_trade_setup(db_session, stock, strategy_version="v1", signal_date=date(2026, 1, 1 + i))

    resp_all = client.get("/api/backtests/v1/trades", params={"limit": 5})
    resp_offset = client.get("/api/backtests/v1/trades", params={"limit": 5, "offset": 3})

    assert resp_all.status_code == 200
    assert resp_offset.status_code == 200
    assert len(resp_offset.json()) == 2
    assert resp_offset.json()[0]["trade_setup_id"] == resp_all.json()[3]["trade_setup_id"]


def test_get_trades_limit_default_is_fifty(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="RELIANCE")
    for i in range(60):
        make_trade_setup(db_session, stock, strategy_version="v1", signal_date=date(2025, 1, 1) + timedelta(days=i))

    resp = client.get("/api/backtests/v1/trades")

    assert resp.status_code == 200
    assert len(resp.json()) == 50


@pytest.mark.parametrize("bad_limit", [0, -1, 1001])
def test_get_trades_rejects_out_of_range_limit(client: TestClient, db_session: Session, bad_limit: int) -> None:
    make_backtest_result(db_session, strategy_version="v1")

    resp = client.get("/api/backtests/v1/trades", params={"limit": bad_limit})

    assert resp.status_code == 422


def test_get_trades_rejects_negative_offset(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")

    resp = client.get("/api/backtests/v1/trades", params={"offset": -1})

    assert resp.status_code == 422


def test_get_trades_score_min_rejects_non_numeric(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")

    resp = client.get("/api/backtests/v1/trades", params={"score_min": "not-a-number"})

    assert resp.status_code == 422


def test_get_trades_empty_result_for_filter_with_no_matches(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="RELIANCE")
    make_trade_setup(db_session, stock, strategy_version="v1", score=10.0)

    resp = client.get("/api/backtests/v1/trades", params={"score_min": 99})

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_trades_entry_date_nullable(client: TestClient, db_session: Session) -> None:
    make_backtest_result(db_session, strategy_version="v1")
    stock = make_stock(db_session, symbol="RELIANCE")
    make_trade_setup(db_session, stock, strategy_version="v1", entry_date=None, entry_price=None)

    resp = client.get("/api/backtests/v1/trades")

    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["entry_date"] is None
    assert row["entry_price"] is None
