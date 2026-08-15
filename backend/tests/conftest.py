"""Shared fixtures for backend tests.

No live DB, no network — every test here is pure-function / in-memory,
matching engine.md's design (engines are stateless functions the caller
feeds truncated bar history into).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest


def make_bars(rows: list[dict], start: date = date(2024, 1, 1)) -> pd.DataFrame:
    """Build an OHLCV DataFrame indexed by date (ascending) from a list of
    dicts with keys open/high/low/close/volume. Dates auto-increment by one
    calendar day per row unless a 'date' key is given explicitly.
    """
    records = []
    current = start
    for row in rows:
        d = row.get("date", current)
        records.append({"date": pd.Timestamp(d), **{k: v for k, v in row.items() if k != "date"}})
        current = d + timedelta(days=1)
    df = pd.DataFrame(records).set_index("date").sort_index()
    return df


def flat_bars(n: int, price: float = 100.0, volume: int = 100_000, start: date = date(2024, 1, 1)) -> pd.DataFrame:
    """n identical OHLCV bars — useful as a base to splice interesting bars onto."""
    return make_bars(
        [{"open": price, "high": price, "low": price, "close": price, "volume": volume} for _ in range(n)],
        start=start,
    )


@pytest.fixture
def bars_factory():
    return make_bars


@pytest.fixture
def flat_bars_factory():
    return flat_bars
