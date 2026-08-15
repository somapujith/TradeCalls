"""Tests for app.data.yfinance_client.

Coverage: the pure `_to_yf_symbol` NSE-suffix helper, and
`fetch_daily_ohlcv`'s retry/backoff logic and no-forward-fill behavior
(rows with any NaN in the required OHLCV columns are skipped, not filled)
— all via monkeypatching `app.data.yfinance_client.yf.download`, never a
real network call.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.data import yfinance_client
from app.data.yfinance_client import OHLCV_COLUMNS, _to_yf_symbol, fetch_daily_ohlcv


# --- _to_yf_symbol ---


def test_to_yf_symbol_appends_ns_suffix_when_missing():
    assert _to_yf_symbol("TCS") == "TCS.NS"


def test_to_yf_symbol_leaves_ns_suffix_untouched_when_present():
    assert _to_yf_symbol("TCS.NS") == "TCS.NS"


def test_to_yf_symbol_handles_symbols_with_dashes():
    assert _to_yf_symbol("M&M") == "M&M.NS"


# --- fetch_daily_ohlcv: happy path ---


def _yf_frame(rows: list[dict], start="2024-01-02") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(rows), freq="D")
    df = pd.DataFrame(rows, index=idx)
    return df


def test_fetch_daily_ohlcv_returns_expected_columns_and_values():
    raw = _yf_frame(
        [
            {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Adj Close": 102.5, "Volume": 1_000_000},
        ]
    )

    with patch.object(yfinance_client.yf, "download", return_value=raw) as mock_download:
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    mock_download.assert_called_once()
    assert list(result.columns) == OHLCV_COLUMNS
    assert len(result) == 1
    row = result.iloc[0]
    assert row["open"] == pytest.approx(100.0)
    assert row["high"] == pytest.approx(105.0)
    assert row["low"] == pytest.approx(99.0)
    assert row["close"] == pytest.approx(103.0)
    assert row["adjusted_close"] == pytest.approx(102.5)
    assert row["volume"] == 1_000_000


def test_fetch_daily_ohlcv_uses_ns_suffixed_symbol_in_download_call():
    raw = _yf_frame([{"Open": 1, "High": 1, "Low": 1, "Close": 1, "Adj Close": 1, "Volume": 1}])

    with patch.object(yfinance_client.yf, "download", return_value=raw) as mock_download:
        fetch_daily_ohlcv("INFY", date(2024, 1, 2), date(2024, 1, 3))

    _, kwargs = mock_download.call_args
    args = mock_download.call_args.args
    called_symbol = args[0] if args else kwargs.get("tickers")
    assert called_symbol == "INFY.NS"


def test_fetch_daily_ohlcv_empty_download_returns_empty_frame_with_columns():
    with patch.object(yfinance_client.yf, "download", return_value=pd.DataFrame()):
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    assert result.empty
    assert list(result.columns) == OHLCV_COLUMNS


def test_fetch_daily_ohlcv_falls_back_to_close_when_adj_close_missing():
    raw = pd.DataFrame(
        [{"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Volume": 500_000}],
        index=pd.date_range("2024-01-02", periods=1),
    )

    with patch.object(yfinance_client.yf, "download", return_value=raw):
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    assert result.iloc[0]["adjusted_close"] == pytest.approx(103.0)


def test_fetch_daily_ohlcv_flattens_multiindex_columns():
    idx = pd.date_range("2024-01-02", periods=1)
    columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Adj Close", "Volume"], ["TCS.NS"]])
    raw = pd.DataFrame([[100.0, 105.0, 99.0, 103.0, 102.5, 1_000_000]], index=idx, columns=columns)

    with patch.object(yfinance_client.yf, "download", return_value=raw):
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    assert len(result) == 1
    assert result.iloc[0]["close"] == pytest.approx(103.0)


# --- fetch_daily_ohlcv: no-forward-fill behavior ---


def test_fetch_daily_ohlcv_skips_rows_with_nan_in_required_columns_not_fill():
    raw = _yf_frame(
        [
            {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Adj Close": 102.5, "Volume": 1_000_000},
            {"Open": np.nan, "High": 106.0, "Low": 100.0, "Close": 104.0, "Adj Close": 103.5, "Volume": 900_000},
            {"Open": 101.0, "High": 107.0, "Low": 100.5, "Close": 105.0, "Adj Close": 104.5, "Volume": 800_000},
        ]
    )

    with patch.object(yfinance_client.yf, "download", return_value=raw):
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 5))

    # the NaN-open row must be dropped entirely, not forward-filled from the prior row
    assert len(result) == 2
    assert 104.0 not in result["close"].values
    closes = sorted(result["close"].tolist())
    assert closes == [103.0, 105.0]


def test_fetch_daily_ohlcv_skips_row_with_nan_volume():
    raw = _yf_frame(
        [
            {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Adj Close": 102.5, "Volume": np.nan},
            {"Open": 101.0, "High": 106.0, "Low": 100.0, "Close": 104.0, "Adj Close": 103.5, "Volume": 900_000},
        ]
    )

    with patch.object(yfinance_client.yf, "download", return_value=raw):
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 4))

    assert len(result) == 1
    assert result.iloc[0]["close"] == pytest.approx(104.0)


def test_fetch_daily_ohlcv_all_nan_rows_returns_empty_result():
    raw = _yf_frame([{"Open": np.nan, "High": np.nan, "Low": np.nan, "Close": np.nan, "Adj Close": np.nan, "Volume": np.nan}])

    with patch.object(yfinance_client.yf, "download", return_value=raw):
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    assert result.empty


# --- fetch_daily_ohlcv: retry/backoff ---


def test_fetch_daily_ohlcv_retries_on_failure_then_succeeds():
    raw = _yf_frame([{"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 103.0, "Adj Close": 102.5, "Volume": 1_000_000}])
    call_count = {"n": 0}

    def flaky_download(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise ConnectionError("simulated network failure")
        return raw

    with patch.object(yfinance_client.yf, "download", side_effect=flaky_download), patch.object(yfinance_client.time, "sleep") as mock_sleep:
        result = fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    assert call_count["n"] == 2
    assert len(result) == 1
    mock_sleep.assert_called()  # backoff slept between attempts, never a real sleep


def test_fetch_daily_ohlcv_raises_after_exhausting_max_retries():
    with patch.object(yfinance_client.yf, "download", side_effect=ConnectionError("down")) as mock_download, patch.object(
        yfinance_client.time, "sleep"
    ):
        with pytest.raises(RuntimeError, match="yfinance fetch failed for TCS"):
            fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    assert mock_download.call_count == yfinance_client.settings.yfinance_max_retries


def test_fetch_daily_ohlcv_does_not_sleep_after_final_failed_attempt():
    with patch.object(yfinance_client.yf, "download", side_effect=ConnectionError("down")), patch.object(
        yfinance_client.time, "sleep"
    ) as mock_sleep:
        with pytest.raises(RuntimeError):
            fetch_daily_ohlcv("TCS", date(2024, 1, 2), date(2024, 1, 3))

    max_retries = yfinance_client.settings.yfinance_max_retries
    assert mock_sleep.call_count == max_retries - 1


def test_fetch_daily_ohlcv_never_hits_real_network(monkeypatch):
    """Guard test: if yf.download is not patched, calling the real network
    function should raise/timeout rather than silently succeed — but since
    we always patch it above, this test just verifies the module-level
    `yf` reference is what gets monkeypatched (regression guard against
    someone importing yf.download directly instead of via the module)."""
    sentinel_called = {"called": False}

    def fake_download(*args, **kwargs):
        sentinel_called["called"] = True
        raise AssertionError("real yf.download must never be invoked in tests")

    monkeypatch.setattr(yfinance_client.yf, "download", fake_download)

    with pytest.raises(RuntimeError):
        fetch_daily_ohlcv("RELIANCE", date(2024, 1, 2), date(2024, 1, 3))

    assert sentinel_called["called"] is True
