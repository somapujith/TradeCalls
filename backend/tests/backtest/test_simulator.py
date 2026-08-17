"""Tests for app.backtest.simulator — the day-by-day, symbol-by-symbol replay
loop that wires market/, breakout/, and backtest/{execution,outcomes,metrics}
together. See docs/engine.md#backtest-simulator--no-look-ahead-by-construction.

The single most important test here is
TestNoLookAheadGuarantee.test_truncating_input_does_not_change_earlier_trade_setups:
running the simulator over days 1..N must produce identical trade_setups for
days 1..N-1 as running it over days 1..N-1 alone. If that test ever fails,
the simulator has a look-ahead bug — full stop, regardless of what anything
else says.

NOTE on setup_type used for the "reaches CONFIRMED" scenarios: this suite's
primary hand-constructed CONFIRMED walkthrough uses a DIP_BUY scenario
(_dip_buy_scenario), but a hand-constructed BREAKOUT scenario
(_breakout_scenario, see TestBreakoutConfirmedEndToEnd) exercises the same
full WATCH -> APPROACHING -> BREAKOUT_ATTEMPT -> CANDLE_CONFIRMATION ->
VOLUME_CONFIRMATION -> CONFIRMED path end-to-end through run_backtest.
app.breakout.breakout_engine now splits resistance lookup into
_resistance_target_above (price >= close, used only by WATCH) and
_resistance_being_tested (nearest by absolute distance, used from
APPROACHING onward) specifically so a close that has moved above a level
doesn't lose that level for the CLOSE_ABOVE_RESISTANCE / candle / volume
checks that follow — this resolved an earlier structural block where
APPROACHING -> BREAKOUT_ATTEMPT was unreachable through the public
resistance_clusters() API. TestBreakoutRecordingLogic still separately
monkeypatches advance_breakout_state for a few orchestration-only cases
(scoring wiring, terminal-state short-circuiting) where driving the real
engine end-to-end would just add noise to what's actually under test.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.backtest.simulator import _indicator_value, _reversal_pattern, run_backtest, run_backtest_and_persist

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


def _touch_high(rows: list[dict], high_price: float, flank_price: float) -> None:
    """Mirror of _touch_low for resistance: 3 flanking bars + 1 high bar + 3
    flanking bars, all below high_price, so the high bar is recognized as a
    genuine SWING_HIGH by market/levels.py's fractal lookback=3 rule. Two
    calls at the same high_price (see _breakout_scenario) form a real
    RESISTANCE_CLUSTER (min_touches=2) rather than an isolated swing high.
    """
    for _ in range(3):
        rows.append({"open": flank_price, "high": flank_price + 0.1, "low": flank_price - 0.3, "close": flank_price - 0.1, "volume": 100_000})
    rows.append({"open": flank_price, "high": high_price, "low": flank_price - 0.2, "close": high_price - 0.2, "volume": 100_000})
    for _ in range(3):
        rows.append({"open": flank_price, "high": flank_price + 0.1, "low": flank_price - 0.3, "close": flank_price - 0.1, "volume": 100_000})


def _breakout_scenario(start: date) -> pd.DataFrame:
    """Hand-constructed: a steady uptrend (60 bars) that touches a real
    resistance level (128.0) twice to form a genuine RESISTANCE_CLUSTER,
    consolidates just under it (within APPROACH_PROXIMITY_PCT), then a
    strong-bodied candle closes decisively above it on rising volume,
    followed by a high-RVOL confirmation bar — walking WATCH ->
    APPROACHING -> BREAKOUT_ATTEMPT -> CANDLE_CONFIRMATION ->
    VOLUME_CONFIRMATION -> CONFIRMED end-to-end through the real
    (unmocked) advance_breakout_state, driven only by bar history exactly
    as run_backtest itself computes resistance_clusters/support_clusters
    per day. Bar-by-bar transitions were verified by direct engine
    inspection before being encoded here (see TestBreakoutConfirmedEndToEnd).
    """
    rows: list[dict] = []
    price = 100.0
    for _ in range(60):
        price += 0.3
        rows.append({"open": price - 0.1, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 100_000})

    resistance_level = 128.0
    _touch_high(rows, resistance_level, 126.0)
    _touch_high(rows, resistance_level, 125.0)

    # Consolidate within 2% below resistance: WATCH -> APPROACHING.
    approach_price = resistance_level * 0.985
    for _ in range(3):
        rows.append({"open": approach_price, "high": approach_price + 0.2, "low": approach_price - 0.2, "close": approach_price, "volume": 100_000})

    # APPROACHING -> BREAKOUT_ATTEMPT: close decisively above resistance.
    rows.append({"open": approach_price, "high": resistance_level + 1.5, "low": approach_price - 0.1, "close": resistance_level + 1.2, "volume": 110_000})
    # BREAKOUT_ATTEMPT -> CANDLE_CONFIRMATION: strong body, low upper wick,
    # closes further above resistance on elevated volume.
    rows.append({"open": resistance_level + 1.2, "high": resistance_level + 3.0, "low": resistance_level + 1.0, "close": resistance_level + 2.8, "volume": 400_000})
    # CANDLE_CONFIRMATION -> VOLUME_CONFIRMATION: RVOL >= 1.5 against the
    # trailing-20 baseline (which by now includes the prior 400k-volume bar).
    rows.append({"open": resistance_level + 2.8, "high": resistance_level + 3.5, "low": resistance_level + 2.5, "close": resistance_level + 3.2, "volume": 900_000})
    # VOLUME_CONFIRMATION -> CONFIRMED: unconditional on the next call.
    rows.append({"open": resistance_level + 3.2, "high": resistance_level + 3.8, "low": resistance_level + 3.0, "close": resistance_level + 3.5, "volume": 200_000})

    # Fill day (T+1 after CONFIRMED): open stays modest/near the planned
    # entry (atr_entry_buffer just above resistance_level) rather than
    # gapping far above it, so attempt_entry_fill's fill_price lands close
    # to planned_entry — a huge gap-up here would fill above target_1r
    # (computed from the CONFIRMED-time planned entry/stop), producing a
    # target ladder that's already "behind" the fill, which is a real and
    # correctly-modeled edge case but not what this scenario is for.
    rows.append({"open": resistance_level + 0.5, "high": resistance_level + 1.5, "low": resistance_level + 0.2, "close": resistance_level + 1.0, "volume": 130_000})
    # Favorable follow-through so outcome tracking has room to resolve.
    for _ in range(6):
        rows.append({"open": resistance_level + 1.0, "high": resistance_level + 2.5, "low": resistance_level + 0.5, "close": resistance_level + 2.0, "volume": 120_000})

    return _make_bars(rows, start)


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


def _dip_buy_scenario_with_target_hit(start: date) -> pd.DataFrame:
    """Same CONFIRMED path as _dip_buy_scenario, but with a modest fill-day
    bar followed by a decisive breakout bar that clears target_1r within the
    tracked window — exercises the pnl-computation branch of _fill_and_track
    (outcome.exit_price is not None), which the base scenario's SESSION_END
    ending never reaches.
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

    rows.append({"open": price, "high": price, "low": price - 1.0, "close": price - 1.5, "volume": 90_000})
    rows.append({"open": price - 1.5, "high": price - 1.5, "low": support_level + 0.8, "close": support_level + 1.2, "volume": 90_000})
    rows.append({"open": support_level + 1.2, "high": support_level + 1.5, "low": support_level + 0.2, "close": support_level + 0.5, "volume": 90_000})
    rows.append({"open": support_level + 0.3, "high": support_level + 0.6, "low": support_level - 1.0, "close": support_level + 0.4, "volume": 400_000})
    rows.append({"open": support_level + 0.4, "high": support_level + 1.0, "low": support_level + 0.2, "close": support_level + 0.8, "volume": 200_000})

    # Fill day: modest range around the prior close, entry fills normally.
    rows.append({"open": 117.0, "high": 117.5, "low": 116.8, "close": 117.2, "volume": 120_000})
    # Decisive follow-through bar clearing target_1r intrabar.
    rows.append({"open": 117.2, "high": 200.0, "low": 117.0, "close": 180.0, "volume": 130_000})
    for _ in range(3):
        rows.append({"open": 180.0, "high": 181.0, "low": 179.0, "close": 180.0, "volume": 100_000})

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


class TestOutcomeTrackingAndPnl:
    def test_target_hit_resolves_with_positive_pnl(self):
        """When a filled trade's forward bars clear a target level,
        _fill_and_track must compute a real pnl (sell - buy - costs), not
        leave it None — None is only correct for still-open/unresolved
        trades (SESSION_END with no exit_price).
        """
        start = date(2024, 1, 1)
        bars = _dip_buy_scenario_with_target_hit(start)
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

        assert row["exit_reason"] == "TARGET_HIT"
        assert row["exit_price"] is not None
        assert row["pnl"] is not None
        assert row["pnl"] > 0
        assert row["exit_price"] > row["entry_price"]


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
    """A real bar-history-only walk to BREAKOUT's CONFIRMED state is covered
    end-to-end by TestBreakoutConfirmedEndToEnd below (see module
    docstring). These tests instead monkeypatch advance_breakout_state
    directly, to verify *this* module's own orchestration logic around a
    CONFIRMED transition (scoring wiring, fill/outcome recording, trade-row
    shape, terminal-state short-circuiting, bars_since_confirmed bookkeeping)
    in isolation from the breakout engine's actual state-transition rules —
    keeping these tests focused on simulator.py's responsibilities rather
    than re-deriving the exact bar shapes needed to reach each state.
    """

    def test_breakout_confirmed_transition_is_recorded_with_sane_trade_row(self, monkeypatch, flat_bars_factory):
        import app.backtest.simulator as simulator_module
        from app.breakout.breakout_engine import EngineResult
        from app.breakout.states import BreakoutState

        start = date(2024, 1, 1)
        bars = flat_bars_factory(90, price=100.0, volume=100_000, start=start)
        nifty = _nifty_close(len(bars) + 5, start)

        call_count = {"n": 0}

        def fake_advance_breakout_state(current_state, bar_history, resistance_clusters, **kwargs):
            call_count["n"] += 1
            # Walk straight from WATCH to VOLUME_CONFIRMATION on the first
            # call, then confirm on the second — matching the two from_state
            # checks the real engine would produce.
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
        # Planned entry was 105.0, but bars are flat at 100.0 so the actual
        # T+1 fill lands near 100, not 105 — a real gap between plan and
        # fill. target_1r must therefore be re-anchored to the actual fill
        # (entry_price + 1R against stop_loss=98.0), not the stale
        # planned-entry-relative 112.0 the engine returned at signal time —
        # see simulator.py's _rebase_targets_to_fill. Recomputed from the
        # row's own entry_price rather than hardcoded, since the exact fill
        # price depends on execution.py's slippage model.
        expected_risk = row["entry_price"] - row["stop_loss"]
        assert row["target_1r"] == pytest.approx(row["entry_price"] + 1.0 * expected_risk)
        assert row["target_1r"] > row["entry_price"]
        assert row["entry_date"] > row["signal_date"]
        assert 0.0 <= row["score"] <= 100.0

    def test_breakout_retest_pending_bars_since_confirmed_counter(self, monkeypatch, flat_bars_factory):
        """_advance_breakout resets breakout_bars_since_confirmed to 0 on
        entering RETEST_PENDING and increments it on every subsequent day
        spent there — verified here via a forced state sequence: WATCH ->
        RETEST_PENDING (enter, counter reset to 0) -> RETEST_PENDING (stay,
        counter -> 1) -> RETEST_PENDING (stay, counter -> 2) -> RETEST_
        CONFIRMED. The counter value is only observable indirectly (it's
        passed as bars_since_confirmed on the next call), so this test
        asserts on the exact sequence of bars_since_confirmed values the
        fake engine observes.
        """
        import app.backtest.simulator as simulator_module
        from app.breakout.breakout_engine import EngineResult
        from app.breakout.states import BreakoutState

        start = date(2024, 1, 1)
        bars = flat_bars_factory(90, price=100.0, volume=100_000, start=start)
        nifty = _nifty_close(len(bars) + 5, start)

        observed_bars_since_confirmed: list[int] = []
        call_count = {"n": 0}

        def fake_advance_breakout_state(current_state, bar_history, resistance_clusters, bars_since_confirmed=0, **kwargs):
            call_count["n"] += 1
            observed_bars_since_confirmed.append(bars_since_confirmed)
            if call_count["n"] == 1:
                return EngineResult(new_state=BreakoutState.RETEST_PENDING.value, transition_event="TEST_ENTER_RETEST")
            if call_count["n"] in (2, 3):
                return EngineResult(new_state=BreakoutState.RETEST_PENDING.value, transition_event=None)
            return EngineResult(new_state=BreakoutState.RETEST_CONFIRMED.value, transition_event="TEST_RETEST_CONFIRMED")

        monkeypatch.setattr(simulator_module, "advance_breakout_state", fake_advance_breakout_state)

        run_backtest(
            bars_by_symbol={"AAA": bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=bars.index[-1].date(),
            nifty_close=nifty,
        )

        # First call: state starts at WATCH (default), counter is the
        # dataclass default 0. Second/third calls: now RETEST_PENDING,
        # counter increments each day. Fourth call: still RETEST_PENDING
        # from_state, counter keeps incrementing right up to the
        # RETEST_CONFIRMED transition (which is itself non-terminal, so a
        # 5th call would follow if the scenario had more days after this
        # point — not needed to prove the counter logic itself).
        assert observed_bars_since_confirmed[:4] == [0, 0, 1, 2]

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


class TestBreakoutConfirmedEndToEnd:
    """Positive control mirroring TestClearSignalProducesConfirmedSetup's
    DIP_BUY coverage, but for BREAKOUT: _breakout_scenario drives the real,
    unmocked advance_breakout_state (via run_backtest's normal per-day
    resistance_clusters/support_clusters computation) all the way from
    WATCH through APPROACHING, BREAKOUT_ATTEMPT, CANDLE_CONFIRMATION,
    VOLUME_CONFIRMATION, to CONFIRMED, and asserts a real trade_setups-shaped
    row comes out the other end of run_backtest with setup_type=BREAKOUT.
    """

    def test_breakout_scenario_produces_confirmed_trade_row(self):
        start = date(2024, 1, 1)
        bars = _breakout_scenario(start)
        nifty = _nifty_close(len(bars) + 5, start)

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

        assert row["symbol"] == "AAA"
        assert row["setup_type"] == "BREAKOUT"
        assert row["strategy_version"] == STRATEGY_VERSION
        assert row["entry_price"] is not None
        assert row["stop_loss"] is not None
        assert row["stop_loss"] < row["entry_price"]
        assert row["target_1r"] > row["entry_price"]
        assert row["entry_date"] > row["signal_date"]
        assert 0.0 <= row["score"] <= 100.0

    def test_breakout_scenario_no_look_ahead(self):
        """Same no-look-ahead invariant as TestNoLookAheadGuarantee, applied
        specifically to the breakout path: truncating the input to the
        CONFIRMED bar (or earlier) must reproduce the same trade_setups the
        full run produces for that same date range.
        """
        start = date(2024, 1, 1)
        full_bars = _breakout_scenario(start)
        nifty_full = _nifty_close(len(full_bars) + 5, start)

        full_trades = run_backtest(
            bars_by_symbol={"AAA": full_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=full_bars.index[-1].date(),
            nifty_close=nifty_full,
        )
        breakout_row = full_trades[full_trades["setup_type"] == "BREAKOUT"].iloc[0]
        confirmed_cutoff = breakout_row["signal_date"]
        # +1 day so the truncated run still includes the fill (T+1) bar —
        # entry_price is only populated once attempt_entry_fill has a next
        # bar to evaluate, same buffer TestNoLookAheadGuarantee uses
        # (DIP_BUY_SIGNAL_INDEX + 2).
        fill_cutoff = confirmed_cutoff + timedelta(days=1)

        truncated_bars = full_bars[full_bars.index.date <= fill_cutoff]
        nifty_truncated = _nifty_close(len(truncated_bars) + 5, start)

        truncated_trades = run_backtest(
            bars_by_symbol={"AAA": truncated_bars},
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=fill_cutoff,
            nifty_close=nifty_truncated,
        )

        truncated_breakout_row = truncated_trades[truncated_trades["setup_type"] == "BREAKOUT"].iloc[0]
        assert truncated_breakout_row["signal_date"] == breakout_row["signal_date"]
        assert truncated_breakout_row["entry_price"] == pytest.approx(breakout_row["entry_price"])
        assert truncated_breakout_row["stop_loss"] == pytest.approx(breakout_row["stop_loss"])
        assert truncated_breakout_row["score"] == pytest.approx(breakout_row["score"])


class TestReversalPatternAdapter:
    """_reversal_pattern adapts across app.market.candles' two possible
    public shapes (see module docstring) — tested by patching attributes
    directly on the real app.market.candles module object (which is what
    _reversal_pattern's `from app.market import candles as candles_module`
    actually resolves against, since the submodule is cached on the parent
    package once imported) so this module's own adapter logic is proven
    independent of whichever shape candles.py currently exposes.
    """

    _BARS = pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3], "volume": [1, 1, 1]})

    def test_uses_has_bullish_reversal_when_present_and_truthy_string(self, monkeypatch):
        import app.market.candles as candles_module

        monkeypatch.setattr(candles_module, "has_bullish_reversal", lambda bars: "HAMMER", raising=False)

        assert _reversal_pattern(self._BARS) == "HAMMER"

    def test_uses_has_bullish_reversal_when_present_and_falsy(self, monkeypatch):
        import app.market.candles as candles_module

        monkeypatch.setattr(candles_module, "has_bullish_reversal", lambda bars: False, raising=False)

        assert _reversal_pattern(self._BARS) is None

    def test_falls_back_to_detect_reversal_pattern(self, monkeypatch):
        import app.market.candles as candles_module

        monkeypatch.delattr(candles_module, "has_bullish_reversal", raising=False)
        monkeypatch.setattr(
            candles_module, "detect_reversal_pattern", lambda bars: {"pattern": "MORNING_STAR", "bar_date": None}
        )

        assert _reversal_pattern(self._BARS) == "MORNING_STAR"

    def test_returns_none_when_candles_module_exposes_neither_function(self, monkeypatch):
        import app.market.candles as candles_module

        monkeypatch.delattr(candles_module, "has_bullish_reversal", raising=False)
        monkeypatch.delattr(candles_module, "detect_reversal_pattern", raising=False)

        assert _reversal_pattern(self._BARS) is None


class TestIndicatorValueHelper:
    def test_returns_none_when_latest_indicators_is_none(self):
        assert _indicator_value(None, "atr") is None

    def test_returns_none_when_column_value_is_nan(self):
        row = pd.Series({"atr": float("nan"), "ema20": 10.0})
        assert _indicator_value(row, "atr") is None

    def test_returns_float_for_present_value(self):
        row = pd.Series({"atr": 2.5, "ema20": 10.0})
        assert _indicator_value(row, "atr") == 2.5


class _FakeSession:
    """Minimal stand-in for a SQLAlchemy Session, dispatching by the ORM
    entity class embedded in the select() query (query.column_descriptions[0]
    ["entity"], stable across .where() clauses) rather than talking to a
    real database. Only implements the subset of the Session API
    run_backtest_and_persist actually calls: scalars().all(), scalar(),
    add(), flush() (no-op), and close() (no-op — this fake never holds a
    real connection, so run_backtest_and_persist's mid-function
    session.close() between the read and write phases, added to release
    the connection during the long pure-compute run_backtest() call, is
    a harmless no-op here).
    """

    def __init__(self, stocks, daily_bars_by_stock_id):
        self._stocks = stocks
        self._daily_bars_by_stock_id = daily_bars_by_stock_id
        self.added: list = []
        self._next_id = 1

    def _entity(self, query):
        return query.column_descriptions[0]["entity"]

    def scalars(self, query):
        from app.db.models import DailyOHLCV, Stock

        entity = self._entity(query)
        if entity is Stock:
            return _FakeScalarResult(list(self._stocks))
        if entity is DailyOHLCV:
            # Only ever queried with a stock_id equality filter in
            # run_backtest_and_persist / _load_bars_df — extract it from the
            # compiled query's bound parameters rather than trying to
            # re-interpret the whole WHERE clause generically.
            stock_id = _extract_equality_param(query, "stock_id")
            return _FakeScalarResult(self._daily_bars_by_stock_id.get(stock_id, []))
        raise NotImplementedError(entity)

    def scalar(self, query):
        results = self.scalars(query).all()
        return results[0] if results else None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        self.added.append(obj)

    def flush(self):
        pass

    def close(self):
        pass


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


def _extract_equality_param(query, column_name: str):
    """SQLAlchemy's compiled bind params carry the literal filter value
    regardless of clause shape — simplest reliable extraction for this
    narrow, single-equality-filter use case (run_backtest_and_persist only
    ever queries DailyOHLCV filtered by a single stock_id).
    """
    compiled = query.compile()
    for key, value in compiled.params.items():
        if key.startswith(column_name):
            return value
    return None


class TestRunBacktestAndPersist:
    """run_backtest_and_persist is the DB-loading/persisting wrapper around
    the pure run_backtest — tested here against a fake in-memory session so
    no real database is required, matching this repo's no-live-DB testing
    convention (see tests/conftest.py's module docstring).
    """

    def _make_stock(self, symbol: str, sector: str | None = "IT", listing_status: str = "ACTIVE"):
        from app.db.models import Stock

        stock = Stock(symbol=symbol, name=symbol, sector=sector, listing_status=listing_status)
        stock.id = abs(hash(symbol)) % 100_000 + 1
        return stock

    def _make_daily_bars(self, stock_id: int, bars: pd.DataFrame) -> list:
        from app.db.models import DailyOHLCV

        rows = []
        for idx, row in bars.iterrows():
            rows.append(
                DailyOHLCV(
                    stock_id=stock_id,
                    trade_date=idx.date() if hasattr(idx, "date") else idx,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    adjusted_close=row["close"],
                    volume=int(row["volume"]),
                )
            )
        return rows

    def test_persists_confirmed_trade_setup_and_backtest_result(self):
        start = date(2024, 1, 1)
        aaa_bars = _dip_buy_scenario(start)
        nifty_stock = self._make_stock("^NSEI", sector=None)
        aaa_stock = self._make_stock("AAA", sector="IT")

        session = _FakeSession(
            stocks=[aaa_stock, nifty_stock],
            daily_bars_by_stock_id={
                aaa_stock.id: self._make_daily_bars(aaa_stock.id, aaa_bars),
                nifty_stock.id: self._make_daily_bars(nifty_stock.id, _nifty_close(len(aaa_bars) + 5, start).to_frame("close").assign(open=lambda d: d["close"], high=lambda d: d["close"], low=lambda d: d["close"], volume=100_000)),
            },
        )

        trades = run_backtest_and_persist(
            session=session,
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=aaa_bars.index[-1].date(),
        )

        assert not trades.empty

        from app.db.models import BacktestResult, TradeSetup

        persisted_setups = [obj for obj in session.added if isinstance(obj, TradeSetup)]
        persisted_results = [obj for obj in session.added if isinstance(obj, BacktestResult)]

        assert len(persisted_setups) == len(trades)
        assert persisted_setups[0].strategy_version == STRATEGY_VERSION
        assert len(persisted_results) == 1
        assert persisted_results[0].total_trades == len(trades)

    def test_symbols_filter_restricts_universe(self):
        start = date(2024, 1, 1)
        aaa_bars = _dip_buy_scenario(start)
        nifty_stock = self._make_stock("^NSEI", sector=None)
        aaa_stock = self._make_stock("AAA", sector="IT")
        bbb_stock = self._make_stock("BBB", sector="PHARMA")

        session = _FakeSession(
            stocks=[aaa_stock, bbb_stock, nifty_stock],
            daily_bars_by_stock_id={
                aaa_stock.id: self._make_daily_bars(aaa_stock.id, aaa_bars),
                bbb_stock.id: self._make_daily_bars(bbb_stock.id, aaa_bars),
                nifty_stock.id: self._make_daily_bars(nifty_stock.id, _nifty_close(len(aaa_bars) + 5, start).to_frame("close").assign(open=lambda d: d["close"], high=lambda d: d["close"], low=lambda d: d["close"], volume=100_000)),
            },
        )

        trades = run_backtest_and_persist(
            session=session,
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=aaa_bars.index[-1].date(),
            symbols=["AAA"],
        )

        assert set(trades["symbol"].unique()) == {"AAA"}

    def test_no_nifty_stock_found_still_returns_empty_result_without_crashing(self):
        start = date(2024, 1, 1)
        aaa_stock = self._make_stock("AAA", sector="IT")
        session = _FakeSession(stocks=[aaa_stock], daily_bars_by_stock_id={})

        trades = run_backtest_and_persist(
            session=session,
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=start + timedelta(days=5),
        )

        assert trades.empty

    def test_trade_row_for_unknown_symbol_is_skipped_defensively(self, monkeypatch):
        """Defensive guard: if run_backtest ever returned a row for a symbol
        not in this run's stock_by_symbol (structurally shouldn't happen
        given bars_by_symbol is itself built from stock_by_symbol, but
        worth guarding since this function crosses the pure/DB boundary),
        run_backtest_and_persist must skip it rather than crash on a
        missing stock lookup.
        """
        import app.backtest.simulator as simulator_module

        start = date(2024, 1, 1)
        aaa_stock = self._make_stock("AAA", sector="IT")
        nifty_stock = self._make_stock("^NSEI", sector=None)
        session = _FakeSession(
            stocks=[aaa_stock, nifty_stock],
            daily_bars_by_stock_id={
                aaa_stock.id: self._make_daily_bars(aaa_stock.id, _dip_buy_scenario(start)),
                nifty_stock.id: [],
            },
        )

        fake_trades = pd.DataFrame(
            [
                {
                    "symbol": "UNKNOWN",
                    "setup_type": "BREAKOUT",
                    "strategy_version": STRATEGY_VERSION,
                    "signal_date": start,
                    "entry_date": None,
                    "entry_price": None,
                    "stop_loss": 10.0,
                    "target_1r": 11.0,
                    "target_1_5r": 11.5,
                    "target_2r": 12.0,
                    "target_3r": 13.0,
                    "nearest_structural_target": None,
                    "score": 50.0,
                    "tier": "C",
                    "sector": None,
                    "regime": "NEUTRAL",
                    "exit_reason": "SESSION_END",
                    "exit_date": None,
                    "exit_price": None,
                    "target_hit": None,
                    "sl_hit": False,
                    "mfe": None,
                    "mae": None,
                    "holding_days": None,
                    "pnl": None,
                }
            ]
        )
        monkeypatch.setattr(simulator_module, "run_backtest", lambda **kwargs: fake_trades)

        from app.db.models import TradeSetup

        trades = run_backtest_and_persist(
            session=session,
            strategy_version=STRATEGY_VERSION,
            start_date=start,
            end_date=start + timedelta(days=5),
        )

        assert len(trades) == 1  # the unknown-symbol row is still returned...
        persisted_setups = [obj for obj in session.added if isinstance(obj, TradeSetup)]
        assert persisted_setups == []  # ...but never persisted, since no matching stock exists
