# Breakout & Backtest Engine

Status: design — not yet implemented. This documents the agreed architecture for the v1 daily-bar backtest baseline (see [AI_Intraday_Breakout_Research_Planning.md](../AI_Intraday_Breakout_Research_Planning.md) for the full future intraday system this is a slice of).

## Scope of v1

Daily bars only. yfinance ingestion. No VWAP/ORB — both need intraday data, added once a live broker feed is wired up. No news engine — news catalyst scoring component defaults to 0 until a news source exists.

## Modules

```
backend/app/
  data/
    yfinance_client.py   — daily OHLCV per symbol, adjusted close, skip missing days (no forward-fill)
    universe.py           — liquidity filter: price > ₹50, 20D avg turnover > ₹10Cr, excludes illiquid/suspended
  market/
    indicators.py         — EMA9/20/50, SMA100/200, RSI, MACD, ATR, Bollinger — computed from history truncated to "today", never sees future bars
    levels.py              — prior day/week/month high-low, 52W high-low, swing high/low, resistance clustering
    relative_strength.py   — stock return vs sector index vs NIFTY
    market_regime.py       — STRONG_BULL..STRONG_BEAR from NIFTY/BANKNIFTY trend + India VIX (or NIFTY historical vol as proxy)
  breakout/
    breakout_engine.py     — state machine, see below
    scoring.py              — breakout score + hard rejection rules
  backtest/
    simulator.py            — event loop, day-by-day, symbol-by-symbol replay
    execution.py             — fills against NEXT bar, applies slippage/brokerage/STT
    outcomes.py              — tracks MFE/MAE/target-hit/SL-hit per trade
    metrics.py                — win rate, profit factor, breakdowns by score bucket/sector/day-of-week/regime
```

## Breakout state machine

```
WATCH → APPROACHING → BREAKOUT_ATTEMPT → CANDLE_CONFIRMATION → VOLUME_CONFIRMATION
→ CONFIRMED → RETEST_PENDING → RETEST_CONFIRMED → TRADE_ACTIVE → TARGET_HIT / INVALIDATED / SESSION_END
```

Stateless per-call function: `(current_state, bar_history_up_to_today, resistance_cluster) → (new_state, transition_event)`. Caller (simulator) owns state storage. The function must never receive bars beyond "today" — this is the core no-look-ahead guarantee and is unit-tested by asserting output is unchanged when future bars are appended to the input.

Confirmation requires: close > resistance trigger AND relative volume confirms AND candle quality (body %, close location) acceptable.

On CONFIRMED: entry = breakout level + ATR-aware buffer; stop-loss = nearest structural support below entry (swing low / resistance-turned-support / consolidation low); targets = 1R/1.5R/2R/3R plus nearest structural resistance.

## Scoring

Weights (VWAP's 10 and news catalyst's 2 excluded in v1 — remaining 88 points renormalized to 100):

| Component | Weight (v1, renormalized) |
|---|---:|
| Resistance breakout | 17 |
| Relative volume | 23 |
| Candle quality | 11 |
| Trend | 11 |
| Retest | 17 |
| Relative strength | 11 |
| Market confirmation | 6 |
| Sector confirmation | 3 |

Interpretation unchanged from original doc: 90-100 A+, 80-89 A, 70-79 B, 60-69 C, <60 ignore.

## Hard rejection rules (override score, not just downgrade)

- Candle does not close above resistance → NOT_CONFIRMED
- RVOL below configured minimum → reject
- Severe upper rejection wick (>60% of range) → reject as fake-breakout risk
- Price closes back below breakout level same/next bar → invalidate

Market regime STRONG_BEAR/BEAR reduces score via a configurable multiplier (not a hard rejection).

## Backtest simulator — no-look-ahead by construction

The simulator walks the trading calendar (derived from dates actually present in `daily_ohlcv`, not an assumed Mon–Fri) day by day, symbol by symbol. Each day, each symbol's bar history is sliced up to and including "today" before being passed to the breakout engine — the code path structurally cannot read tomorrow's bar while deciding today's state transition.

Entry fills happen on the bar **after** the signal bar, never the signal bar itself. If next-day open gaps past the stop-loss, the trade is not filled — it's logged as `INVALIDATED_GAP` (not silently dropped, to avoid survivorship bias in the stats).

## Cost model

Slippage in basis points off next-day open, scaled by the symbol's ATR. Brokerage (flat or %, configurable). STT 0.025% intraday sell-side (configurable — exact rate should be re-verified against current NSE rules before trusting backtest P&L).

## Reproducibility

Every `backtest_results` row is tagged with a `strategy_version` string (a short git commit hash) so re-running after tuning `scoring.py` weights doesn't silently overwrite prior runs — needed to compare strategy versions against each other later.

## Known v1 limitations (carried forward from gap analysis)

- Daily bars only — no intraday confirmation (VWAP, ORB, RVOL-by-time-bucket)
- No news catalyst scoring (news engine not built yet)
- No walk-forward weight optimization — that's a separate follow-up spec, not in this slice
- No portfolio-level risk (correlation between simultaneous signals, max concurrent positions) — v1 backtests each signal independently
- Survivorship bias: universe pulled from current NSE listings only, not point-in-time historical listings
