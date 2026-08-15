"""Tests for POST /api/backtest-runs — see app/api/backtest_runs.py.

Not documented in docs/api.md's v1 endpoint table (that doc covers the read
endpoints only), but is part of the FastAPI route layer under test per this
task's scope, and app/api/deps.py's generate_strategy_version() is exercised
almost exclusively through this route.

Key contract points under test:
- 202 Accepted with {strategy_version, start_date, end_date, status: "queued"}.
- start_date/end_date default when omitted (end_date -> today, start_date ->
  end_date - 365 days).
- 400 if start_date >= end_date (explicit or defaulted).
- The actual backtest run is queued as a BackgroundTask, not run inline —
  we monkeypatch app.api.backtest_runs._run_backtest_job itself (the
  function object background_tasks.add_task is given) so the real
  SessionLocal()-backed job (which would hit the live Postgres DATABASE_URL)
  never executes during tests.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.backtest_runs as backtest_runs_module
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    """No DB dependency override needed for the endpoint itself — the actual
    background job (which would open a real SessionLocal()) is monkeypatched
    out per-test instead. Plain TestClient, no lifespan."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _stub_background_job(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Prevents the real _run_backtest_job (which opens a live-DB SessionLocal
    session) from ever running during these tests. Starlette's TestClient
    executes BackgroundTasks synchronously after the response is built, so
    without this stub every POST here would attempt a real DB connection.
    """
    captured: dict = {}

    def fake_job(strategy_version: str, start_date: date, end_date: date) -> None:
        captured["strategy_version"] = strategy_version
        captured["start_date"] = start_date
        captured["end_date"] = end_date

    monkeypatch.setattr(backtest_runs_module, "_run_backtest_job", fake_job)
    return captured


def test_trigger_backtest_run_defaults(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post("/api/backtest-runs", json={})

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["end_date"] == date.today().isoformat()
    assert body["start_date"] == (date.today() - timedelta(days=365)).isoformat()
    assert isinstance(body["strategy_version"], str)
    assert body["strategy_version"]


def test_trigger_backtest_run_explicit_dates(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post(
        "/api/backtest-runs",
        json={"start_date": "2025-01-01", "end_date": "2025-06-01"},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["start_date"] == "2025-01-01"
    assert body["end_date"] == "2025-06-01"


def test_trigger_backtest_run_400_when_start_after_end(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post(
        "/api/backtest-runs",
        json={"start_date": "2025-06-01", "end_date": "2025-01-01"},
    )

    assert resp.status_code == 400
    assert isinstance(resp.json()["detail"], str)


def test_trigger_backtest_run_400_when_start_equals_end(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post(
        "/api/backtest-runs",
        json={"start_date": "2025-06-01", "end_date": "2025-06-01"},
    )

    assert resp.status_code == 400


def test_trigger_backtest_run_malformed_date_422(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post(
        "/api/backtest-runs",
        json={"start_date": "not-a-date", "end_date": "2025-06-01"},
    )

    assert resp.status_code == 422


def test_trigger_backtest_run_queues_background_job_with_matching_args(
    client: TestClient, _stub_background_job: dict
) -> None:
    resp = client.post(
        "/api/backtest-runs",
        json={"start_date": "2025-01-01", "end_date": "2025-06-01"},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert _stub_background_job["strategy_version"] == body["strategy_version"]
    assert _stub_background_job["start_date"] == date(2025, 1, 1)
    assert _stub_background_job["end_date"] == date(2025, 6, 1)


def test_trigger_backtest_run_partial_dates_only_start(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post("/api/backtest-runs", json={"start_date": "2025-01-01"})

    assert resp.status_code == 202
    assert resp.json()["start_date"] == "2025-01-01"
    assert resp.json()["end_date"] == date.today().isoformat()


def test_trigger_backtest_run_partial_dates_only_end(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post("/api/backtest-runs", json={"end_date": "2025-06-01"})

    assert resp.status_code == 202
    assert resp.json()["end_date"] == "2025-06-01"
    assert resp.json()["start_date"] == (date(2025, 6, 1) - timedelta(days=365)).isoformat()


def test_trigger_backtest_run_response_shape(client: TestClient, _stub_background_job: dict) -> None:
    resp = client.post("/api/backtest-runs", json={})

    assert resp.status_code == 202
    assert set(resp.json().keys()) == {"strategy_version", "start_date", "end_date", "status"}
