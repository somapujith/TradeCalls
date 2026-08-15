"""Tests for app.backtest.simulator — the day-by-day, symbol-by-symbol replay
loop that wires market/, breakout/, and backtest/{execution,outcomes,metrics}
together. See docs/engine.md#backtest-simulator--no-look-ahead-by-construction.

The single most important test here is
TestNoLookAheadGuarantee.test_truncating_input_does_not_change_earlier_trade_setups:
running the simulator over days 1..N must produce identical trade_setups for
days 1..N-1 as running it over days 1..N-1 alone. If that test ever fails,
the simulator has a look-ahead bug — full stop, regardless of what anything
else says.

NOTE on setup_type used for the "reaches CONFIRMED" scenarios: this suite
uses a hand-constructed DIP_BUY scenario (not BREAKOUT) as the "clear signal
produces a CONFIRMED trade_setup with sane entry/SL/targets" test, because
app.breakout.breakout_engine._nearest_resistance (owned by a different,
concurrently-edited module — not in this file's scope) only ever returns
candidate resistance levels with `price >= close`, while advance_breakout_
state's APPROACHING -> BREAKOUT_ATTEMPT transition requires `close >
target.price` against that same target. Those two conditions are mutually
exclusive by construction, which makes APPROACHING -> BREAKOUT_ATTEMPT
structurally unreachable through the public resistance_clusters() API as it
stands today (independently confirmed by 3 pre-existing failures in
tests/test_breakout_engine.py at the time this file was written). The
dip-buy engine's equivalent (_nearest_support, approached from above) does
not have this issue, so it is used here to exercise the full CONFIRMED path
end-to-end. The breakout path is still covered by this file's negative
controls (no false positives) and by feeding the engine bar histories that
exercise every breakout state reachable today.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.backtest.simulator import run_backtest

STRATEGY_VERSION = "abc1234"


def _nifty_close(n: int, start: date) -> pd.Series:
    """Flat NIFTY series long enough to cover any bar_history length used
    below — market_regime classification just needs a value per date.
    """
    dates = pd.date_range(start=pd.Timestamp(start), periods=n, freq="D")
    return pd.Series([20000.0] * n, index=dates)


def _make_bars(rows: list[dict], start: date) -> pd.DataFrame:
    records = []
    current = start
    for row in rows:
        records.append({"date": pd.Timestamp(current), **row})
        current = current + timedelta(days=1)
    return pd.DataFrame(records).set_index("date").sort_index()


def _touch_low(rows: list[dict], low_price: float, flank_price: float) -> None:
    """3 flanking bars + 1 low bar + 3 flanking bars: satisfies swing_highs_
    lows' fractal lookback=3 requirement so the low bar is recognized as a
    real SWING_LOW by market/levels.py.
    """
    for _ in range(3):
        rows.append({"open": flank_price, "high": flank_price + 0.3, "low": flank_price - 0.1, "close": flank_price + 0.1, "volume": 100_000})
    rows.append({"open": flank_price, "high": flank_price, "low": low_price, "close": low_price + 0.2, "volume": 100_000})
    for _ in range(3):
        rows.append({"open": flank_price, "high": flank_price + 0.3, "low": flank_price - 0.1, "close": flank_price + 0.1, "volume": 100_000})


def _dip_buy_scenario(start: date) -> pd.DataFrame:
    """Hand-constructed: a steady uptrend (60 bars, EMA20 > EMA50, price
    trending up) that touches a real support level (116.0) twice to form a
    genuine SUPPORT_CLUSTER, then a further uptrend leg, then a controlled
    pullback that tests that support with a hammer reversal on rising
    volume — walking UPTREND_CONFIRMED -> PULLBACK_IN_PROGRESS ->
    SUPPORT_TEST -> REVERSAL_CANDLE -> VOLUME_CONFIRMATION -> CONFIRMED.

    Everything before the pullback is deliberately a clean, unambiguous
    uptrend so the precondition and support cluster aren't accidents of
    noise, and the exact bar-by-bar transition sequence was verified by
    direct engine inspection before being encoded here.
    """
    rows: list[dict] = []
    price = 100.0
    for _ in range(60):
        price += 0.3
        rows.append({"open": price - 0.1, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 100_000})

    support_level = 116.0
    _touch_low(rows, support_level, 118.0)
    _touch_low(rows, support_level, 119.0)

    for _ in range(5):
        price += 0.4
        rows.append({"open": price - 0.1, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 100_000})

    # PULLBACK_IN_PROGRESS: close starts dropping below prior close.
    rows.append({"open": price, "high": price, "low": price - 1.0, "close": price - 1.5, "volume": 90_000})
    # Continues toward support, still well within the uptrend precondition.
    rows.append({"open": price - 1.5, "high": price - 1.5, "low": support_level + 0.8, "close": support_level + 1.2, "volume": 90_000})
    # SUPPORT_TEST: within 1.5% of the 116.0 cluster, no close below it.
    rows.append({"open": support_level + 1.2, "high": support_level + 1.5, "low": support_level + 0.2, "close": support_level + 0.5, "volume": 90_000})
    # REVERSAL_CANDLE: hammer — long lower wick below support, closes back
    # above it, with a volume spike that also drives RVOL_CONFIRMED.
    rows.append({"open": support_level + 0.3, "high": support_level + 0.6, "low": support_level - 1.0, "close": support_level + 0.4, "volume": 400_000})
    # VOLUME_CONFIRMATION -> CONFIRMED: elevated volume persists one more bar.
    rows.append({"open": support_level + 0.4, "high": support_level + 1.0, "low": support_level + 0.2, "close": support_level + 0.8, "volume": 200_000})

    # Favorable follow-through so outcome tracking has room to resolve.
    for _ in range(6):
        rows.append({"open": 118.0, "high": 122.0, "low": 117.5, "close": 121.0, "volume": 120_000})

    return _make_bars(rows, start)


# Index of the CONFIRMED signal bar within _dip_buy_scenario's output,
# verified by direct engine inspection (see module docstring).
DIP_BUY_SIGNAL_INDEX = 84


class TestNoSignalBeforeEnoughHistory:
    def test_no_trade_setups_when_history_shorter_than_minimum(self, flat_bars_factory):
        """20 bars is nowhere near enough for resistance/support clustering
        (needs swing points from 2*lookback+1=7+ bars, plus warmed-up
        indicators) — the simulator must not fire anything this early.
        """
        start = date(2024, 1, 1)
        bars = flat_bars_factory(20, price=100.0, volume=100_000, start=start)
        nifty = _nifty_close(20, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        assert trades.empty

    def test_no_confirmed_setup_fires_before_min_history_bars_elapsed(self):
        """Even with a real dip-buy pattern present, if min_history_bars is
        set impossibly high, the simulator must never let any day pass the
        history-length gate, so nothing can ever confirm.
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
            min_history_bars=1000,  # impossible threshold -> nothing should ever confirm
        )

        assert trades.empty


class TestNoLookAheadGuarantee:
    def test_truncating_input_does_not_change_earlier_trade_setups(self):
        """THE most important test in this file. Running the simulator over
        the full date range and over a truncated prefix of the same date
        range must produce identical trade setup rows (by signal_date) for
        every day that exists in both runs. If truncating the future changes
        a past decision, the simulator is leaking look-ahead information.
        """
        start = date(2024, 1, 1)
        full_bars = _dip_buy_scenario(start)

        # Truncate to just after the CONFIRMED signal bar so the
        # RETEST/TRADE_ACTIVE machinery differs between runs, but everything
        # up to and including CONFIRMED should be identical.
        truncated_bars = full_bars.iloc[: DIP_BUY_SIGNAL_INDEX + 2]

        nifty_full = _nifty_close(len(full_bars) + 5, start)
        nifty_truncated = _nifty_close(len(truncated_bars) + 5, start)

        trades_full = run_backtest(
            bars_by_symbol={"AAA": full_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=full_bars.index[-1].date(),
            nifty_close=nifty_full,
        )
        trades_truncated = run_backtest(
            bars_by_symbol={"AAA": truncated_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=truncated_bars.index[-1].date(),
            nifty_close=nifty_truncated,
        )

        full_signal_dates = sorted(trades_full["signal_date"].dropna().tolist())
        truncated_signal_dates = sorted(trades_truncated["signal_date"].dropna().tolist())

        assert full_signal_dates, "expected at least one confirmed setup in the full run"
        assert full_signal_dates == truncated_signal_dates

        # Entry price / stop-loss / score for that shared setup must match
        # exactly too — not just its existence.
        full_row = trades_full.iloc[0]
        truncated_row = trades_truncated.iloc[0]
        assert full_row["stop_loss"] == truncated_row["stop_loss"]
        assert full_row["score"] == truncated_row["score"]
        assert full_row["entry_price"] == truncated_row["entry_price"]

    def test_running_prefix_alone_matches_prefix_of_full_run(self):
        """Complementary framing of the same guarantee: run the simulator
        ONLY over days 1..N (no future bars ever supplied at all) and assert
        it's identical to the days-1..N subset of the days-1..N_total run.
        """
        start = date(2024, 1, 1)
        full_bars = _dip_buy_scenario(start)
        prefix_bars = full_bars.iloc[: DIP_BUY_SIGNAL_INDEX + 1]  # cuts off right at CONFIRMED

        nifty_full = _nifty_close(len(full_bars) + 5, start)
        nifty_prefix = _nifty_close(len(prefix_bars) + 5, start)

        trades_full = run_backtest(
            bars_by_symbol={"AAA": full_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=full_bars.index[-1].date(),
            nifty_close=nifty_full,
        )
        trades_prefix_only = run_backtest(
            bars_by_symbol={"AAA": prefix_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=prefix_bars.index[-1].date(),
            nifty_close=nifty_prefix,
        )

        confirmed_signal_date = full_bars.index[DIP_BUY_SIGNAL_INDEX].date()
        full_match = trades_full[trades_full["signal_date"] == confirmed_signal_date]
        prefix_match = trades_prefix_only[trades_prefix_only["signal_date"] == confirmed_signal_date]

        assert not full_match.empty
        assert not prefix_match.empty
        assert float(full_match.iloc[0]["stop_loss"]) == float(prefix_match.iloc[0]["stop_loss"])
        assert float(full_match.iloc[0]["score"]) == float(prefix_match.iloc[0]["score"])


class TestClearSignalProducesConfirmedSetup:
    def test_hand_constructed_dip_buy_produces_sane_confirmed_setup(self):
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        dip_buy_trades = trades[trades["setup_type"] == "DIP_BUY"]
        assert len(dip_buy_trades) == 1

        row = dip_buy_trades.iloc[0]
        assert row["strategy_version"] == STRATEGY_VERSION
        assert row["symbol"] == "AAA"
        assert row["signal_date"] == bars.index[DIP_BUY_SIGNAL_INDEX].date()

        # Entry near the reversal candle's close, stop-loss below the dip's
        # own low (not a wider structural stop, per docs/engine.md), targets
        # forming an ascending ladder above entry.
        assert row["stop_loss"] < row["entry_price"]
        assert row["target_1r"] > row["entry_price"]
        assert row["target_1_5r"] > row["target_1r"]
        assert row["target_2r"] > row["target_1_5r"]
        assert row["target_3r"] > row["target_2r"]

        # Score must be a real number in [0, 100], not a placeholder.
        assert 0.0 <= row["score"] <= 100.0

    def test_flat_history_produces_no_confirmed_setup_of_either_type(self, flat_bars_factory):
        """Negative control: pure flat consolidation for the same total
        length as the dip-buy scenario — must never confirm anything since
        there's no uptrend, no pullback, no breakout.
        """
        start = date(2024, 1, 1)
        bars = flat_bars_factory(90, price=100.0, volume=100_000, start=start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        assert trades.empty

    def test_pure_uptrend_without_pullback_never_confirms_dip_buy(self):
        """Negative control: a clean uptrend with NO pullback at all must
        never reach past PULLBACK_IN_PROGRESS — a dip-buy requires an actual
        dip, not just an uptrend.
        """
        start = date(2024, 1, 1)
        rows: list[dict] = []
        price = 100.0
        for _ in range(90):
            price += 0.3
            rows.append({"open": price - 0.1, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 100_000})
        bars = _make_bars(rows, start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        assert trades.empty


class TestEntryFillsOnTPlusOne:
    def test_entry_date_is_strictly_after_signal_date(self):
        """Per execution.py's attempt_entry_fill contract and docs/engine.md:
        entry fills happen on the bar AFTER the signal (CONFIRMED) bar, never
        the signal bar itself.
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        dip_buy_trades = trades[trades["setup_type"] == "DIP_BUY"]
        assert len(dip_buy_trades) == 1
        row = dip_buy_trades.iloc[0]

        assert row["entry_date"] is not None
        assert row["signal_date"] is not None
        assert row["entry_date"] > row["signal_date"]

        expected_signal_date = bars.index[DIP_BUY_SIGNAL_INDEX].date()
        assert row["signal_date"] == expected_signal_date

        # Fill happens strictly on the next trading day present in the
        # calendar (T+1), not two or more days later.
        symbol_dates = [d.date() for d in bars.index]
        signal_idx = symbol_dates.index(expected_signal_date)
        expected_entry_date = symbol_dates[signal_idx + 1]
        assert row["entry_date"] == expected_entry_date

    def test_gap_below_stop_loss_invalidates_without_fill(self):
        """If next-day open gaps below the stop-loss, attempt_entry_fill
        returns filled=False and the simulator must log INVALIDATED_GAP
        rather than silently dropping the setup (no survivorship bias).
        """
        start = date(2024, 1, 1)
        rows: list[dict] = []
        price = 100.0
        for _ in range(60):
            price += 0.3
            rows.append({"open": price - 0.1, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 100_000})

        support_level = 116.0
        _touch_low(rows, support_level, 118.0)
        _touch_low(rows, support_level, 119.0)

        for _ in range(5):
            price += 0.4
            rows.append({"open": price - 0.1, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 100_000})

        rows.append({"open": price, "high": price, "low": price - 1.0, "close": price - 1.5, "volume": 90_000})
        rows.append({"open": price - 1.5, "high": price - 1.5, "low": support_level + 0.8, "close": support_level + 1.2, "volume": 90_000})
        rows.append({"open": support_level + 1.2, "high": support_level + 1.5, "low": support_level + 0.2, "close": support_level + 0.5, "volume": 90_000})
        rows.append({"open": support_level + 0.3, "high": support_level + 0.6, "low": support_level - 1.0, "close": support_level + 0.4, "volume": 400_000})
        rows.append({"open": support_level + 0.4, "high": support_level + 1.0, "low": support_level + 0.2, "close": support_level + 0.8, "volume": 200_000})
        # Catastrophic gap-down through the stop-loss on the fill day.
        rows.append({"open": 50.0, "high": 51.0, "low": 48.0, "close": 49.0, "volume": 200_000})
        for _ in range(3):
            rows.append({"open": 49.0, "high": 50.0, "low": 48.0, "close": 49.0, "volume": 100_000})

        bars = _make_bars(rows, start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        dip_buy_trades = trades[trades["setup_type"] == "DIP_BUY"]
        assert len(dip_buy_trades) == 1
        row = dip_buy_trades.iloc[0]
        assert row["exit_reason"] == "INVALIDATED_GAP"
        assert row["entry_date"] is None
        assert pd.isna(row["entry_price"]) or row["entry_price"] is None


class TestBothSetupTypesRunIndependently:
    def test_symbol_can_be_in_non_terminal_state_for_both_setups_simultaneously(self):
        """A symbol progressing through the dip-buy state chain is
        simultaneously evaluated by the breakout engine every day (still in
        WATCH/APPROACHING, non-terminal) — both engines run every day
        without one blocking the other, and only the dip-buy setup
        confirms (this scenario has no breakout resistance clearance).
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        setup_types = set(trades["setup_type"].unique())
        assert setup_types == {"DIP_BUY"}

    def test_multi_symbol_backtest_runs_independently_per_symbol(self, flat_bars_factory):
        start = date(2024, 1, 1)
        dip_buy_bars = _dip_buy_scenario(start)
        flat_bars = flat_bars_factory(len(dip_buy_bars), price=50.0, volume=80_000, start=start)
        nifty = _nifty_close(len(dip_buy_bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": dip_buy_bars, "BBB": flat_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=dip_buy_bars.index[-1].date(),
            nifty_close=nifty,
        )

        assert set(trades["symbol"].unique()) == {"AAA"}


class TestOutputShapeMatchesMetricsContract:
    def test_columns_required_by_compute_backtest_metrics_are_present(self):
        """compute_backtest_metrics expects at minimum: score, sector,
        entry_date, exit_reason, mfe, mae, holding_days, regime, pnl.
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
            stock_meta={"AAA": {"sector": "IT"}},
        )

        required_columns = {
            "score",
            "sector",
            "entry_date",
            "exit_reason",
            "mfe",
            "mae",
            "holding_days",
            "regime",
            "pnl",
        }
        assert required_columns.issubset(set(trades.columns))

        dip_buy_row = trades[trades["setup_type"] == "DIP_BUY"].iloc[0]
        assert dip_buy_row["sector"] == "IT"

    def test_empty_universe_returns_empty_dataframe_with_no_error(self):
        start = date(2024, 1, 1)
        nifty = _nifty_close(10, start)

        trades = run_backtest(
            bars_by_symbol={},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=start + timedelta(days=10),
            nifty_close=nifty,
        )

        assert isinstance(trades, pd.DataFrame)
        assert trades.empty

    def test_is_pure_function_same_input_same_output(self):
        """No hidden state / randomness — calling run_backtest twice with
        identical inputs must yield identical outputs (deterministic replay,
        no DB I/O, no wall-clock dependence in the returned rows).
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

        kwargs = dict(
            bars_by_symbol={"AAA": bars.copy()},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty.copy(),
        )

        trades_1 = run_backtest(**kwargs)
        trades_2 = run_backtest(**kwargs)

        pd.testing.assert_frame_equal(trades_1.reset_index(drop=True), trades_2.reset_index(drop=True))


class TestStrategyVersionTagging:
    def test_every_trade_row_tagged_with_supplied_strategy_version(self):
        """docs/engine.md#reproducibility: every row must carry the caller-
        supplied strategy_version verbatim — simulator.py must not compute
        or mutate it.
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)
        custom_version = "deadbee"

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=custom_version,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        assert not trades.empty
        assert (trades["strategy_version"] == custom_version).all()


class TestOptionalMarketRegimeInputs:
    """banknifty_close, sector_close_by_symbol, and india_vix_by_date are all
    optional plumbing straight through to classify_market_regime /
    relative_strength — exercised here to make sure supplying them doesn't
    change the pure-function contract or break truncation.
    """

    def test_accepts_banknifty_close_without_error(self):
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)
        banknifty = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
            banknifty_close=banknifty,
        )

        assert not trades.empty

    def test_accepts_sector_close_by_symbol_without_error(self):
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)
        sector_series = _nifty_close(len(bars) + 5, start)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
            sector_close_by_symbol={"AAA": sector_series},
            stock_meta={"AAA": {"sector": "IT"}},
        )

        assert not trades.empty
        assert (trades["sector"] == "IT").all()

    def test_accepts_india_vix_by_date_override_without_error(self):
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)
        vix_by_date = {d.date(): 15.0 for d in bars.index}

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
            india_vix_by_date=vix_by_date,
        )

        assert not trades.empty


class TestBreakoutRecordingLogic:
    """The breakout engine's own APPROACHING -> BREAKOUT_ATTEMPT transition
    is currently unreachable via the public engine (see module docstring),
    which structurally blocks any bar-history-only test from driving a
    symbol to BREAKOUT's CONFIRMED state. To still verify *this* module's
    CONFIRMED-handling logic for the breakout path (scoring wiring, fill/
    outcome recording, trade-row shape) independently of that upstream
    engine bug, these tests monkeypatch advance_breakout_state itself so
    only app.backtest.simulator's own orchestration code is under test.
    """

    def test_breakout_confirmed_transition_is_recorded_with_sane_trade_row(self, monkeypatch, flat_bars_factory):
        import app.backtest.simulator as simulator_module
        from app.breakout.breakout_engine import EngineResult
        from app.breakout.states import BreakoutState

        start = date(2024, 1, 1)
        bars = flat_bars_factory(90, price=100.0, volume=100_000, start=start)
        nifty = _nifty_close(len(bars) + 5, start)

        call_count = {"n": 0}
        real_advance = simulator_module.advance_breakout_state

        def fake_advance_breakout_state(current_state, bar_history, resistance_clusters, **kwargs):
            call_count["n"] += 1
            # Walk straight from WATCH to VOLUME_CONFIRMATION on the first
            # call, then confirm on the second — matching the two from_state
            # checks the real engine would produce, but bypassing the
            # unreachable APPROACHING->BREAKOUT_ATTEMPT hop.
            if call_count["n"] == 1:
                return EngineResult(new_state=BreakoutState.VOLUME_CONFIRMATION.value, transition_event="TEST_FORCED")
            return EngineResult(
                new_state=BreakoutState.CONFIRMED.value,
                transition_event="CONFIRMED",
                entry_price=105.0,
                stop_loss=98.0,
                targets={"target_1r": 112.0, "target_1_5r": 115.5, "target_2r": 119.0, "target_3r": 126.0, "nearest_structural_target": None},
            )

        monkeypatch.setattr(simulator_module, "advance_breakout_state", fake_advance_breakout_state)

        trades = run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        breakout_trades = trades[trades["setup_type"] == "BREAKOUT"]
        assert len(breakout_trades) == 1
        row = breakout_trades.iloc[0]
        assert row["entry_price"] is not None
        assert row["stop_loss"] == 98.0
        assert row["target_1r"] == 112.0
        assert row["entry_date"] > row["signal_date"]
        assert 0.0 <= row["score"] <= 100.0

    def test_breakout_terminal_state_stops_advancing(self, monkeypatch, flat_bars_factory):
        """Once a symbol's breakout state machine reaches a terminal state
        (e.g. NOT_CONFIRMED), the simulator must stop calling
        advance_breakout_state for that symbol on subsequent days — this is
        the is_terminal short-circuit at the top of _advance_breakout.
        """
        import app.backtest.simulator as simulator_module
        from app.breakout.breakout_engine import EngineResult
        from app.breakout.states import BreakoutState

        start = date(2024, 1, 1)
        bars = flat_bars_factory(90, price=100.0, volume=100_000, start=start)
        nifty = _nifty_close(len(bars) + 5, start)

        call_count = {"n": 0}

        def fake_advance_breakout_state(current_state, bar_history, resistance_clusters, **kwargs):
            call_count["n"] += 1
            return EngineResult(new_state=BreakoutState.NOT_CONFIRMED.value, transition_event="TEST_FORCED_TERMINAL")

        monkeypatch.setattr(simulator_module, "advance_breakout_state", fake_advance_breakout_state)

        run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        # min_history_bars=60 default means the engine is only called once
        # the symbol first has >=60 bars; after that one call it goes
        # terminal and must never be called again for the remaining days.
        assert call_count["n"] == 1
