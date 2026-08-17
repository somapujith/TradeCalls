"""Backtest event loop: walks the trading calendar day-by-day, symbol-by-
symbol, wiring market/, breakout/, and backtest/{execution,outcomes,metrics}
together — see docs/engine.md#backtest-simulator--no-look-ahead-by-construction.

Pure function, no DB I/O: takes in-memory bar history and returns a
DataFrame of completed trades. Persistence (writing trade_setups,
breakout_events, breakout_candidates, backtest_results rows) is a separate
caller/scheduler concern, per docs/backend.md's separation of concerns —
this mirrors execution.py/outcomes.py/metrics.py, which are all pure
functions with no DB access.

No-look-ahead is structural, not a runtime check: the trading calendar is
derived once from dates actually present in the input bars (never an
assumed Mon-Fri calendar), and on each day the ONLY bar_history ever handed
to either state machine is bars_by_symbol[symbol] sliced to rows with
date <= "today". Nothing downstream of that slice operation can reach a
future bar even by accident, because the future rows are never in the
object references it holds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.backtest.execution import attempt_entry_fill, trade_costs
from app.backtest.metrics import compute_backtest_metrics
from app.backtest.outcomes import track_trade_outcome
from app.breakout.breakout_engine import advance_breakout_state
from app.breakout.dip_buy_engine import advance_dip_buy_state
from app.breakout.rvol import relative_volume
from app.breakout.scoring import score_breakout, score_dip_buy
from app.breakout.states import BreakoutState, DipBuyState, is_terminal
from app.market.candles import candle_quality
from app.market.indicators import compute_indicators
from app.market.levels import resistance_clusters as compute_resistance_clusters
from app.market.levels import support_clusters as compute_support_clusters
from app.market.market_regime import classify_market_regime
from app.market.relative_strength import relative_strength

SETUP_BREAKOUT = "BREAKOUT"
SETUP_DIP_BUY = "DIP_BUY"

# Enough for EMA50/ATR14 to be warm and for swing-high/low clustering to have
# a meaningful sample before any state is allowed to advance past WATCH /
# UPTREND_CONFIRMED. Not tied to SMA200's warm-up — v1 doesn't gate signals
# on SMA200 per docs/engine.md's indicator list being informational, not all
# state-machine inputs.
MIN_HISTORY_BARS = 60

TRADE_COLUMNS = [
    "symbol",
    "setup_type",
    "strategy_version",
    "signal_date",
    "entry_date",
    "entry_price",
    "stop_loss",
    "target_1r",
    "target_1_5r",
    "target_2r",
    "target_3r",
    "nearest_structural_target",
    "score",
    "tier",
    "sector",
    "regime",
    "exit_reason",
    "exit_date",
    "exit_price",
    "target_hit",
    "sl_hit",
    "mfe",
    "mae",
    "holding_days",
    "pnl",
]


@dataclass
class _SymbolState:
    """Per-symbol, per-setup-type state carried across days. Owned entirely
    by the simulator — the state machines themselves are stateless, per
    docs/engine.md's "(current_state, bar_history, ...) -> (new_state,
    transition_event)" contract.
    """

    breakout_state: str = BreakoutState.WATCH.value
    dip_buy_state: str = DipBuyState.UPTREND_CONFIRMED.value
    breakout_bars_since_confirmed: int = 0
    dip_buy_bars_since_confirmed: int = 0
    dip_buy_pullback_low: float | None = None


def run_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    strategy_version: str,
    start_date: date,
    end_date: date,
    nifty_close: pd.Series,
    stock_meta: dict[str, dict] | None = None,
    sector_close_by_symbol: dict[str, pd.Series] | None = None,
    banknifty_close: pd.Series | None = None,
    india_vix_by_date: dict[date, float] | None = None,
    min_history_bars: int = MIN_HISTORY_BARS,
) -> pd.DataFrame:
    """Full replay for one strategy_version over [start_date, end_date].

    bars_by_symbol: symbol -> OHLCV DataFrame indexed by date ascending,
        columns [open, high, low, close, volume]. May extend beyond
        end_date (needed so CONFIRMED setups near the window's edge still
        have forward bars to resolve fills/outcomes against) — the state
        machines themselves only ever see rows <= "today" regardless.
    strategy_version: short git commit hash, tagging every returned row per
        docs/engine.md#reproducibility. Computed by the caller, not here.
    stock_meta: symbol -> {"sector": str | None}, used only for the
        `sector` column consumed by compute_backtest_metrics' breakdowns.
    nifty_close / banknifty_close: ascending-by-date price series for market
        regime classification. May extend beyond end_date; truncated to
        "today" internally on every iteration.
    sector_close_by_symbol: symbol -> sector index close series, for
        relative_strength's rs_vs_sector component. Optional.
    india_vix_by_date: optional {date: vix_close} override for market
        regime's volatility input; falls back to realized NIFTY vol.

    Returns a DataFrame, one row per completed (CONFIRMED) trade setup
    across both setup types, with at least the columns compute_backtest_
    metrics requires (score, sector, entry_date, exit_reason, mfe, mae,
    holding_days, regime, pnl) plus identifying/diagnostic columns. Empty
    DataFrame (with TRADE_COLUMNS) if no setup ever confirmed.
    """
    stock_meta = stock_meta or {}
    sector_close_by_symbol = sector_close_by_symbol or {}

    calendar = _trading_calendar(bars_by_symbol, start_date, end_date)
    symbol_states: dict[str, _SymbolState] = {symbol: _SymbolState() for symbol in bars_by_symbol}
    trade_rows: list[dict] = []

    for today in calendar:
        nifty_today = _truncate_series(nifty_close, today)
        banknifty_today = _truncate_series(banknifty_close, today) if banknifty_close is not None else None
        vix_value = india_vix_by_date.get(today) if india_vix_by_date else None
        # classify_market_regime expects a Series (it reads .empty and
        # .iloc[-1]), not a bare float — india_vix_by_date's per-date dict
        # shape is the more convenient contract for callers, so adapt here.
        vix_today = pd.Series([vix_value]) if vix_value is not None else None
        regime_result = classify_market_regime(nifty_today, banknifty_today, vix_today)
        regime = regime_result["regime"]

        for symbol, full_history in bars_by_symbol.items():
            # The single line where no-look-ahead is enforced: every
            # downstream computation for "today" only ever touches this
            # slice, never full_history. full_history itself is only
            # referenced later, after CONFIRMED, purely to walk FORWARD
            # bars for fill/outcome resolution — which is legitimate
            # (the trade literally executes and resolves across real
            # future bars in a backtest), not a look-ahead violation in
            # the state-machine's *decision* of whether to confirm.
            truncated = _truncate_bars(full_history, today)
            if len(truncated) < min_history_bars:
                continue

            state = symbol_states[symbol]
            sector = (stock_meta.get(symbol) or {}).get("sector")

            indicators = compute_indicators(truncated)
            latest_indicators = indicators.iloc[-1] if not indicators.empty else None
            resistance = compute_resistance_clusters(truncated)
            support = compute_support_clusters(truncated)

            sector_series = sector_close_by_symbol.get(symbol)
            sector_today = _truncate_series(sector_series, today) if sector_series is not None else None
            rs = relative_strength(truncated["close"], sector_today, nifty_today)

            _advance_breakout(
                symbol=symbol,
                today=today,
                truncated=truncated,
                full_history=full_history,
                resistance=resistance,
                support=support,
                latest_indicators=latest_indicators,
                rs=rs,
                regime=regime,
                sector=sector,
                state=state,
                strategy_version=strategy_version,
                trade_rows=trade_rows,
            )
            _advance_dip_buy(
                symbol=symbol,
                today=today,
                truncated=truncated,
                full_history=full_history,
                resistance=resistance,
                support=support,
                latest_indicators=latest_indicators,
                rs=rs,
                regime=regime,
                sector=sector,
                state=state,
                strategy_version=strategy_version,
                trade_rows=trade_rows,
            )

    if not trade_rows:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    return pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)


def _trading_calendar(bars_by_symbol: dict[str, pd.DataFrame], start: date, end: date) -> list[date]:
    """Derived from dates actually present in the supplied bars across the
    whole universe, never an assumed Mon-Fri calendar — docs/engine.md.
    """
    all_dates: set[date] = set()
    for df in bars_by_symbol.values():
        for idx in df.index:
            d = idx.date() if hasattr(idx, "date") else idx
            if start <= d <= end:
                all_dates.add(d)
    return sorted(all_dates)


def _truncate_bars(bars: pd.DataFrame, today: date) -> pd.DataFrame:
    return bars[bars.index.date <= today]


def _truncate_series(series: pd.Series | None, today: date) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    return series[series.index.date <= today]


def _as_date(idx) -> date:
    return idx.date() if hasattr(idx, "date") else idx


def _advance_breakout(
    *,
    symbol: str,
    today: date,
    truncated: pd.DataFrame,
    full_history: pd.DataFrame,
    resistance,
    support,
    latest_indicators,
    rs: dict,
    regime,
    sector: str | None,
    state: _SymbolState,
    strategy_version: str,
    trade_rows: list[dict],
) -> None:
    from_state = state.breakout_state
    if is_terminal(from_state):
        return

    swing_lows = [lvl for lvl in support if lvl.level_type in ("SWING_LOW", "SUPPORT_CLUSTER")]
    atr = _indicator_value(latest_indicators, "atr")

    result = advance_breakout_state(
        current_state=from_state,
        bar_history=truncated,
        resistance_clusters=resistance,
        support_levels=swing_lows,
        atr=atr,
        bars_since_confirmed=state.breakout_bars_since_confirmed,
    )

    if result.new_state == BreakoutState.RETEST_PENDING.value and from_state != BreakoutState.RETEST_PENDING.value:
        state.breakout_bars_since_confirmed = 0
    elif from_state == BreakoutState.RETEST_PENDING.value:
        state.breakout_bars_since_confirmed += 1

    state.breakout_state = result.new_state

    if result.new_state == BreakoutState.CONFIRMED.value and from_state == BreakoutState.VOLUME_CONFIRMATION.value:
        today_bar = truncated.iloc[-1]
        cq = candle_quality(float(today_bar["open"]), float(today_bar["high"]), float(today_bar["low"]), float(today_bar["close"]))
        rvol = relative_volume(truncated["volume"])
        ema20 = _indicator_value(latest_indicators, "ema20")
        ema50 = _indicator_value(latest_indicators, "ema50")
        trend_aligned = bool(ema20 is not None and ema50 is not None and ema20 > ema50)

        score = score_breakout(
            resistance_breakout_fired=True,
            rvol=rvol,
            candle_body_pct=cq.body_pct,
            trend_aligned=trend_aligned,
            retest_held=False,
            relative_strength_vs_nifty=rs.get("rs_vs_nifty") if rs else None,
            market_regime=regime,
            sector_confirmed=bool(rs and rs.get("rs_vs_sector") and rs["rs_vs_sector"] > 0),
        )

        _record_confirmed_trade(
            symbol=symbol,
            setup_type=SETUP_BREAKOUT,
            strategy_version=strategy_version,
            signal_date=today,
            engine_result=result,
            score_result=score,
            sector=sector,
            regime=regime,
            atr_on_signal_date=atr,
            full_history=full_history,
            trade_rows=trade_rows,
        )


def _advance_dip_buy(
    *,
    symbol: str,
    today: date,
    truncated: pd.DataFrame,
    full_history: pd.DataFrame,
    resistance,
    support,
    latest_indicators,
    rs: dict,
    regime,
    sector: str | None,
    state: _SymbolState,
    strategy_version: str,
    trade_rows: list[dict],
) -> None:
    from_state = state.dip_buy_state
    if is_terminal(from_state):
        return

    if from_state in (DipBuyState.PULLBACK_IN_PROGRESS.value, DipBuyState.SUPPORT_TEST.value):
        low_today = float(truncated["low"].iloc[-1])
        state.dip_buy_pullback_low = (
            low_today if state.dip_buy_pullback_low is None else min(state.dip_buy_pullback_low, low_today)
        )

    ema20 = _indicator_value(latest_indicators, "ema20")
    ema50 = _indicator_value(latest_indicators, "ema50")
    atr = _indicator_value(latest_indicators, "atr")

    result = advance_dip_buy_state(
        current_state=from_state,
        bar_history=truncated,
        support_clusters=support,
        resistance_clusters=resistance,
        ema20=ema20,
        ema50=ema50,
        atr=atr,
        pullback_low=state.dip_buy_pullback_low,
        bars_since_confirmed=state.dip_buy_bars_since_confirmed,
    )

    if from_state == DipBuyState.RETEST_PENDING.value:
        state.dip_buy_bars_since_confirmed += 1

    state.dip_buy_state = result.new_state

    if result.new_state == DipBuyState.CONFIRMED.value and from_state == DipBuyState.VOLUME_CONFIRMATION.value:
        trend_strength = 1.0 if (ema20 is not None and ema50 is not None and ema20 > ema50) else 0.0
        rvol = relative_volume(truncated["volume"])
        reversal_pattern = _reversal_pattern(truncated)

        score = score_dip_buy(
            support_hold_quality=1.0,
            rvol=rvol,
            reversal_pattern=reversal_pattern,
            trend_strength=trend_strength,
            retest_held=False,
            relative_strength_vs_nifty=rs.get("rs_vs_nifty") if rs else None,
            market_regime=regime,
            sector_confirmed=bool(rs and rs.get("rs_vs_sector") and rs["rs_vs_sector"] > 0),
        )

        _record_confirmed_trade(
            symbol=symbol,
            setup_type=SETUP_DIP_BUY,
            strategy_version=strategy_version,
            signal_date=today,
            engine_result=result,
            score_result=score,
            sector=sector,
            regime=regime,
            atr_on_signal_date=atr,
            full_history=full_history,
            trade_rows=trade_rows,
        )
        state.dip_buy_pullback_low = None


def _reversal_pattern(bar_history: pd.DataFrame) -> str | None:
    """app.market.candles exposes reversal-pattern detection under different
    names depending on how far along its own concurrent implementation is
    (has_bullish_reversal per docs/engine.md's module contract vs.
    detect_reversal_pattern's {"pattern": ...} dict shape seen in this repo
    at the time this module was written) — support both so this module
    doesn't hard-fail on a naming difference that isn't this module's to fix.
    """
    from app.market import candles as candles_module

    if hasattr(candles_module, "has_bullish_reversal"):
        pattern = candles_module.has_bullish_reversal(bar_history.tail(3))
        return pattern if isinstance(pattern, str) else (None if not pattern else "REVERSAL")
    if hasattr(candles_module, "detect_reversal_pattern"):
        return candles_module.detect_reversal_pattern(bar_history.tail(3)).get("pattern")
    return None


def _indicator_value(latest_indicators, column: str) -> float | None:
    if latest_indicators is None:
        return None
    value = latest_indicators.get(column)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def _record_confirmed_trade(
    *,
    symbol: str,
    setup_type: str,
    strategy_version: str,
    signal_date: date,
    engine_result,
    score_result,
    sector: str | None,
    regime,
    atr_on_signal_date: float | None,
    full_history: pd.DataFrame,
    trade_rows: list[dict],
) -> None:
    planned_targets = engine_result.targets or {}
    fill_outcome = _fill_and_track(
        signal_date=signal_date,
        full_history=full_history,
        entry_price=engine_result.entry_price,
        stop_loss=engine_result.stop_loss,
        targets=planned_targets,
        atr_on_signal_date=atr_on_signal_date,
    )
    # Targets actually tracked against outcome resolution — rebased to the
    # real fill price by _fill_and_track when a fill occurred (see
    # _rebase_targets_to_fill), otherwise the original signal-time plan
    # (no fill means no rebasing reference point).
    recorded_targets = fill_outcome["targets"]

    regime_label = regime.value if hasattr(regime, "value") else str(regime)

    trade_rows.append(
        {
            "symbol": symbol,
            "setup_type": setup_type,
            "strategy_version": strategy_version,
            "signal_date": signal_date,
            "entry_date": fill_outcome["entry_date"],
            "entry_price": fill_outcome["entry_price"],
            "stop_loss": engine_result.stop_loss,
            "target_1r": recorded_targets.get("target_1r"),
            "target_1_5r": recorded_targets.get("target_1_5r"),
            "target_2r": recorded_targets.get("target_2r"),
            "target_3r": recorded_targets.get("target_3r"),
            "nearest_structural_target": recorded_targets.get("nearest_structural_target"),
            "score": score_result.total,
            "tier": score_result.tier,
            "sector": sector,
            "regime": regime_label,
            "exit_reason": fill_outcome["exit_reason"],
            "exit_date": fill_outcome["exit_date"],
            "exit_price": fill_outcome["exit_price"],
            "target_hit": fill_outcome["target_hit"],
            "sl_hit": fill_outcome["sl_hit"],
            "mfe": fill_outcome["mfe"],
            "mae": fill_outcome["mae"],
            "holding_days": fill_outcome["holding_days"],
            "pnl": fill_outcome["pnl"],
        }
    )


def _rebase_targets_to_fill(planned_targets: dict, planned_entry: float, fill_price: float, stop_loss: float) -> dict:
    """Re-anchor the R-multiple target ladder to the actual fill price.

    engine_result.targets is computed at signal time against the *planned*
    entry (breakout level / reversal close + ATR buffer). The real fill can
    land meaningfully away from that plan — a gap-up open, slippage — and
    since stop_loss is structural and does NOT move with the fill, reusing
    the planned R-multiples verbatim can put a "target" below the actual
    entry once the fill price has moved past where the plan assumed it
    would. Re-deriving 1R/1.5R/2R/3R off (fill_price, stop_loss) keeps the
    risk multiple meaning intact; nearest_structural_target is left as-is
    since it's anchored to resistance/support structure, not to entry price,
    and doesn't need to move with a modest fill-price difference.
    """
    if planned_entry <= 0 or fill_price == planned_entry:
        return planned_targets
    risk = fill_price - stop_loss
    return {
        "target_1r": fill_price + 1.0 * risk,
        "target_1_5r": fill_price + 1.5 * risk,
        "target_2r": fill_price + 2.0 * risk,
        "target_3r": fill_price + 3.0 * risk,
        "nearest_structural_target": planned_targets.get("nearest_structural_target"),
    }


def _fill_and_track(
    *,
    signal_date: date,
    full_history: pd.DataFrame,
    entry_price: float | None,
    stop_loss: float,
    targets: dict,
    atr_on_signal_date: float | None,
) -> dict:
    """Entry fills against the bar AFTER signal_date, never the signal bar
    itself (docs/engine.md). full_history here is intentionally the FULL
    (non-truncated) per-symbol frame: once a setup has CONFIRMED on
    signal_date, resolving its fill/outcome legitimately walks forward
    through bars that are chronologically after the decision was made —
    this is the backtest replaying realized history forward from a fixed
    decision point, not the state machine peeking ahead to make that
    decision in the first place.

    Returns the fill-rebased targets dict too (key "targets"), so the caller
    persists the ladder that was actually tracked against, not the stale
    signal-time plan.
    """
    future = full_history[full_history.index.date > signal_date]
    if future.empty:
        return _no_fill_result(exit_reason="SESSION_END", exit_date=None, targets=targets)

    next_bar = future.iloc[0]
    planned_entry = float(entry_price) if entry_price is not None else float(next_bar["open"])
    fill = attempt_entry_fill(
        next_bar_open=float(next_bar["open"]),
        next_bar_high=float(next_bar["high"]),
        next_bar_low=float(next_bar["low"]),
        planned_entry=planned_entry,
        stop_loss=float(stop_loss),
        atr=atr_on_signal_date,
    )

    if not fill.filled:
        return _no_fill_result(exit_reason=fill.reason or "INVALIDATED_GAP", exit_date=_as_date(future.index[0]), targets=targets)

    entry_date = _as_date(future.index[0])
    bars_after_entry = future.iloc[1:]
    rebased_targets = _rebase_targets_to_fill(targets, planned_entry, float(fill.fill_price), float(stop_loss))
    outcome = track_trade_outcome(
        entry_date=entry_date,
        entry_price=float(fill.fill_price),
        stop_loss=float(stop_loss),
        targets=rebased_targets,
        bars_after_entry=bars_after_entry,
    )

    pnl = None
    if outcome.exit_price is not None:
        buy_value = float(fill.fill_price)
        sell_value = float(outcome.exit_price)
        costs = trade_costs(buy_value, sell_value)
        # Fractional return (e.g. 0.05 = +5%), not raw rupee difference —
        # see outcomes.py's module docstring for why. A ₹30,000 stock's
        # raw rupee P&L otherwise dwarfs a ₹120 stock's at the same %
        # move, distorting metrics.py's profit_factor toward whichever
        # trades happened to be in expensive stocks.
        pnl = ((sell_value - buy_value) - costs.total) / buy_value

    return {
        "entry_date": entry_date,
        "entry_price": fill.fill_price,
        "exit_reason": outcome.exit_reason,
        "exit_date": outcome.exit_date,
        "exit_price": outcome.exit_price,
        "target_hit": outcome.target_hit,
        "sl_hit": outcome.sl_hit,
        "mfe": outcome.mfe,
        "mae": outcome.mae,
        "holding_days": outcome.holding_days,
        "pnl": pnl,
        "targets": rebased_targets,
    }


def _no_fill_result(*, exit_reason: str, exit_date: date | None, targets: dict) -> dict:
    return {
        "entry_date": None,
        "entry_price": None,
        "exit_reason": exit_reason,
        "exit_date": exit_date,
        "exit_price": None,
        "target_hit": None,
        "sl_hit": False,
        "mfe": None,
        "mae": None,
        "holding_days": None,
        "pnl": None,
        "targets": targets,
    }


# --- DB-loading wrapper -----------------------------------------------------
# run_backtest above is intentionally pure/DB-free (docs/backend.md's
# separation of concerns — mirrors execution.py/outcomes.py/metrics.py).
# Both callers that need a real run against Postgres (the POST
# /api/backtest-runs background task in app/api/backtest_runs.py, and the
# daily breakout-state-advance job in app/scheduler.py) go through this
# wrapper instead of calling run_backtest directly, so there's exactly one
# place that knows how to load bars from daily_ohlcv and persist the
# resulting trades — not duplicated per caller.

NIFTY_SYMBOL = "^NSEI"


def run_backtest_and_persist(
    session,
    strategy_version: str,
    start_date: date,
    end_date: date,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    """Loads bars for `symbols` (default: all ACTIVE, non-index stocks) plus
    NIFTY from daily_ohlcv, runs the pure run_backtest, then persists one
    TradeSetup (+ TradeOutcome if closed) per returned row and a single
    aggregated BacktestResult. Returns the same DataFrame run_backtest did,
    so callers that just want in-process metrics don't have to re-query.

    Caller owns the transaction (commit/rollback) — this function only
    adds/flushes, consistent with the rest of app/db usage in this repo.
    """
    from sqlalchemy import select

    from app.db.models import BacktestResult, BreakoutEvent, Stock, TradeOutcome, TradeSetup

    stock_query = select(Stock).where(Stock.listing_status == "ACTIVE")
    stocks = session.scalars(stock_query).all()
    if symbols is not None:
        wanted = set(symbols)
        stocks = [s for s in stocks if s.symbol in wanted]

    stock_by_symbol = {s.symbol: s for s in stocks}
    bars_by_symbol = {symbol: _load_bars_df(session, stock.id) for symbol, stock in stock_by_symbol.items()}
    bars_by_symbol = {sym: df for sym, df in bars_by_symbol.items() if not df.empty}

    nifty_stock = session.scalar(select(Stock).where(Stock.symbol == NIFTY_SYMBOL))
    nifty_close = _load_bars_df(session, nifty_stock.id)["close"] if nifty_stock is not None else pd.Series(dtype=float)

    stock_meta = {symbol: {"sector": stock.sector} for symbol, stock in stock_by_symbol.items()}
    # Extract the one scalar the write loop below actually needs (stock.id)
    # into a plain dict BEFORE releasing the session — after session.close()
    # these ORM objects are detached and any attribute access on them raises
    # DetachedInstanceError rather than lazily reloading.
    stock_id_by_symbol = {symbol: stock.id for symbol, stock in stock_by_symbol.items()}

    # run_backtest() below is pure CPU-bound compute with zero DB usage —
    # for a large universe (hundreds of symbols x years of daily bars) it
    # can run long enough that holding this connection idle through it gets
    # the connection killed by Neon's serverless proxy before the write
    # phase even starts (observed live 2026-08-17, reproduced identically
    # across multiple fresh sessions — pool_pre_ping alone doesn't help
    # here since it only re-validates at pool CHECKOUT, and the connection
    # was still "checked out" the whole idle stretch). Closing here releases
    # it back to the pool; the write loop below re-acquires on first use,
    # which IS a genuine new checkout and gets pool_pre_ping's protection.
    session.close()

    trades = run_backtest(
        bars_by_symbol=bars_by_symbol,
        strategy_version=strategy_version,
        start_date=start_date,
        end_date=end_date,
        nifty_close=nifty_close,
        stock_meta=stock_meta,
    )

    for row in trades.to_dict(orient="records"):
        stock_id = stock_id_by_symbol.get(row["symbol"])
        if stock_id is None:
            continue

        state = row["exit_reason"] if row["exit_reason"] else ("TRADE_ACTIVE" if row["entry_date"] else "CONFIRMED")
        setup = TradeSetup(
            stock_id=stock_id,
            setup_type=row["setup_type"],
            strategy_version=strategy_version,
            signal_date=row["signal_date"],
            entry_date=row["entry_date"],
            entry_price=row["entry_price"],
            stop_loss=row["stop_loss"],
            target_1r=row["target_1r"],
            target_1_5r=row["target_1_5r"],
            target_2r=row["target_2r"],
            target_3r=row["target_3r"],
            nearest_structural_target=row["nearest_structural_target"],
            score=row["score"],
            score_breakdown=None,
            state=state,
        )
        session.add(setup)
        session.flush()

        session.add(
            BreakoutEvent(
                stock_id=stock_id,
                setup_type=row["setup_type"],
                trade_date=row["signal_date"],
                from_state=None,
                to_state="CONFIRMED",
                transition_event="CONFIRMED",
            )
        )

        if row["exit_reason"] is not None:
            session.add(
                TradeOutcome(
                    trade_setup_id=setup.id,
                    mfe=row["mfe"],
                    mae=row["mae"],
                    target_hit=row["target_hit"],
                    sl_hit=bool(row["sl_hit"]),
                    exit_price=row["exit_price"],
                    exit_date=row["exit_date"],
                    exit_reason=row["exit_reason"],
                    holding_days=row["holding_days"],
                )
            )

    metrics = compute_backtest_metrics(trades) if not trades.empty else None
    session.add(
        BacktestResult(
            strategy_version=strategy_version,
            setup_type=None,
            run_date=date.today(),
            start_date=start_date,
            end_date=end_date,
            total_trades=int(len(trades)),
            win_rate=metrics.win_rate if metrics else None,
            profit_factor=metrics.profit_factor if metrics else None,
            avg_mfe=metrics.avg_mfe if metrics else None,
            avg_mae=metrics.avg_mae if metrics else None,
            avg_holding_days=metrics.avg_holding_days if metrics else None,
            breakdown_by_score_bucket=json.dumps(metrics.breakdown_by_score_bucket) if metrics else None,
            breakdown_by_sector=json.dumps(metrics.breakdown_by_sector) if metrics else None,
            breakdown_by_day_of_week=json.dumps(metrics.breakdown_by_day_of_week) if metrics else None,
            breakdown_by_regime=json.dumps(metrics.breakdown_by_regime) if metrics else None,
        )
    )

    return trades


def _load_bars_df(session, stock_id: int) -> pd.DataFrame:
    from sqlalchemy import select

    from app.db.models import DailyOHLCV

    rows = session.scalars(
        select(DailyOHLCV).where(DailyOHLCV.stock_id == stock_id).order_by(DailyOHLCV.trade_date.asc())
    ).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        [
            {
                "date": r.trade_date,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": int(r.volume),
            }
            for r in rows
        ]
    )
    return df.set_index(pd.to_datetime(df["date"])).drop(columns=["date"])
