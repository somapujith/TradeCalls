# Breakout & Dip-Buy Backtest Engine

Status: design — not yet implemented. This documents the agreed architecture for the v1 daily-bar backtest baseline (see [AI_Intraday_Breakout_Research_Planning.md](../AI_Intraday_Breakout_Research_Planning.md) for the original intraday research doc — superseded on timeframe/budget per [Zero-Budget Constraint](#zero-budget-constraint) below).

## Zero-Budget Constraint

**Hard rule: every component in this system must be free.** No paid data feeds, no paid brokers, no paid hosting, no paid APIs.

This no longer rules out live/intraday capability — see [Live Data: Angel One](#live-data-angel-one) below. **Angel One's SmartAPI is free** (no subscription, ₹0 brokerage on API orders) and its official Python SDK (`smartapi-python`) supports a WebSocket live price/order feed in addition to REST quotes — Angel One likely offers this WebSocket feed on the same free terms as the REST API (unverified against a live account at documentation time; verify against SmartAPI's own docs/bulletins before relying on it). This replaces the earlier assumption that any live feed necessarily costs money (that assumption held for Zerodha Kite Connect ₹2000/mo, which is why intraday was dropped in the original version of this doc — it does not hold for Angel One SmartAPI).

Requires an Angel One trading/demat account (opening one is free; standard KYC applies) and generating an API key from the SmartAPI developer portal (smartapi.angelone.in). This is an account-linked API, not an anonymous public one, so it identifies as this user's broker account. Auth is two-factor: the account's MPIN plus a rotating TOTP code computed from a base32 secret issued once when TOTP 2FA is enabled on the SmartAPI portal (the `pyotp` package computes the current code at login time) — see `app/data/angel_one_client.py`'s module docstring for the exact `generateSession(client_code, mpin, totp)` call shape. Rate limits are not fully documented publicly as of this writing — verify against the live SDK/docs at implementation time rather than assuming the details below are exhaustive.

NSE official data (announcements, filings, corporate actions) still has no free *official* API. Where used, it goes through unofficial free wrapper libraries (e.g. `nsepython`/`nsetools`-style scrapers of NSE's public site) — free but grey-area against NSE's terms of use, and fragile. Treat this layer as best-effort context, not a dependable data source.

## Scope of v1

Daily bars only. yfinance ingestion (free, no API key). No VWAP/ORB/RVOL-by-time-bucket in v1 — these need intraday bars, which is now a live-data capability (see [Live Data: Angel One](#live-data-angel-one)) but not yet wired into the breakout/dip-buy engines; that's the v-next slice, kept separate from v1's daily-bar backtest baseline so the already-designed no-look-ahead backtest machinery isn't disturbed. No news engine yet — news catalyst scoring component defaults to 0 until a free news source is wired in (candidate: free RSS feeds + unofficial NSE announcement scraping).

v1 covers **two setup types**, not just breakout — see [Dip-Buy Setup](#dip-buy-setup) below. Both are designed daily-bar-first; the live-data layer below extends them with a current-price overlay now, and is the foundation for a future live-scanning mode once the daily-bar version has backtested track record.

## Live Data: Angel One

Free broker API used for **current price display** in v1 (see [frontend.md](frontend.md)) and reserved as the foundation for live intraday scanning in a later version. Two distinct capabilities, intentionally not conflated:

| Capability | v1 usage | Data flow |
|---|---|---|
| REST quote (`ltpData`) | On-demand or polled (e.g. once per minute while dashboard is open) to show current price next to each open call | `data/angel_one_client.py` → dashboard, does **not** feed the breakout/dip-buy engines |
| WebSocket live tick feed | Not used in v1 | Angel One likely offers a WebSocket feed alongside SmartAPI's REST quotes (unverified — not confirmed against a live account); if so, reserved for v-next live scanner — see [Future: Live Intraday Scanning](#future-live-intraday-scanning) |

This split matters: v1's breakout/dip-buy detection and backtesting stay entirely on yfinance daily bars — deterministic, already-designed, reproducible via `strategy_version`. Angel One's LTP is a **display-only overlay** in v1 — it shows what the price is doing right now next to a call that was generated from yesterday's close, it does not change the call itself. Wiring live ticks into the detection engines is new work (see Future section) and should not be assumed to work simply because the data is now available for free.

`data/angel_one_client.py` module: wraps `smartapi-python`'s `SmartConnect`, handles login/session (two-factor: MPIN + TOTP, see Zero-Budget Constraint above), exposes `get_ltp(symbol, exchange, symbol_token) -> {price, timestamp}`. Credentials (`angel_one_api_key`/`angel_one_client_code`/`angel_one_mpin`/`angel_one_totp_secret`) go in `config.py` per [backend.md](backend.md), never hardcoded.

**Known v1 limitation**: Angel One's `ltpData` call is keyed by exchange + a numeric `symboltoken`, not a bare NSE ticker — there is no scrip-master lookup wired in yet to resolve a symbol like `RELIANCE` to its Angel One instrument token. `get_ltp` therefore requires the caller to supply `symbol_token` explicitly and raises `AngelOneRequestError` if it's omitted, rather than guessing (a wrong token would silently quote the wrong instrument). Building a lookup against Angel One's published scrip master JSON is future work, not yet done.

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

## Setup types

v1 supports two independent setup detectors sharing the same bar history, indicator, and level infrastructure. Each has its own state machine, scoring weights, and rejection rules — they are scored and stored separately (`trade_setups.setup_type` discriminates them, see [db.md](db.md)) and a symbol can trigger both on different days, or neither.

### Breakout state machine

```
WATCH → APPROACHING → BREAKOUT_ATTEMPT → CANDLE_CONFIRMATION → VOLUME_CONFIRMATION
→ CONFIRMED → RETEST_PENDING → RETEST_CONFIRMED → TRADE_ACTIVE → TARGET_HIT / INVALIDATED / SESSION_END
```

Stateless per-call function: `(current_state, bar_history_up_to_today, resistance_cluster) → (new_state, transition_event)`. Caller (simulator) owns state storage. The function must never receive bars beyond "today" — this is the core no-look-ahead guarantee and is unit-tested by asserting output is unchanged when future bars are appended to the input.

Confirmation requires: close > resistance trigger AND relative volume confirms AND candle quality (body %, close location) acceptable.

On CONFIRMED: entry = breakout level + ATR-aware buffer; stop-loss = nearest structural support below entry (swing low / resistance-turned-support / consolidation low); targets = 1R/1.5R/2R/3R plus nearest structural resistance.

### Dip-Buy setup

Buy a pullback within an established uptrend, not a breakout above resistance. Opposite entry logic to breakout (buying weakness, not strength) — must not reuse breakout's scoring weights as-is.

Precondition (must hold before a dip is even considered): symbol is in a confirmed uptrend — price above EMA50, EMA20 above EMA50, no lower-low structure on the daily swing sequence (per `market/levels.py` swing high/low). Without this precondition, a pullback is just a downtrend continuation, not a dip-buy.

```
UPTREND_CONFIRMED → PULLBACK_IN_PROGRESS → SUPPORT_TEST → REVERSAL_CANDLE → VOLUME_CONFIRMATION
→ CONFIRMED → RETEST_PENDING → RETEST_CONFIRMED → TRADE_ACTIVE → TARGET_HIT / INVALIDATED / SESSION_END
```

Same stateless-function shape and no-look-ahead guarantee as breakout: `(current_state, bar_history_up_to_today, support_cluster) → (new_state, transition_event)`.

`SUPPORT_TEST` triggers when price pulls back into a support cluster (EMA20/EMA50, prior swing low, or previous resistance turned support) without closing below it. `REVERSAL_CANDLE` requires a bullish reversal pattern (hammer, bullish engulfing, or morning star per `market/candles.py`) closing back above the support level. `VOLUME_CONFIRMATION` requires RVOL confirming the reversal bar specifically (volume expanding on the up-day, not the down-days of the pullback) — a dip that reverses on low volume is not confirmed.

On CONFIRMED: entry = reversal candle's close (or a small ATR-aware buffer above it, same buffer function as breakout); stop-loss = below the dip's low (the lowest point of the pullback, not a wider structural stop — a dip-buy that revisits its own low has failed); targets = 1R/1.5R/2R/3R plus nearest structural resistance (same target ladder logic as breakout, reusing `levels.py`).

## Scoring

### Breakout weights

VWAP and news catalyst excluded in v1 (no intraday data / no news engine yet — see Zero-Budget Constraint and Scope of v1 above); the remaining components are reweighted to sum to 99, not rescaled to a clean 100 — a market-regime multiplier (see Hard rejection rules below) is applied on top and can push the final score above 100 for a STRONG_BULL regime, so the raw component sum doesn't need to hit exactly 100 on its own. Implemented in `breakout/scoring.py`'s `BREAKOUT_WEIGHTS`:

| Component | Weight (v1) |
|---|---:|
| Resistance breakout | 17 |
| Relative volume | 23 |
| Candle quality | 11 |
| Trend | 11 |
| Retest | 17 |
| Relative strength | 11 |
| Market confirmation | 6 |
| Sector confirmation | 3 |
| **Total** | **99** |

### Dip-buy weights

Same exclusions as breakout (no VWAP, no news catalyst) but reweighted — trend strength and reversal-candle quality matter more than breakout volume does for this setup; "resistance breakout" is replaced by "support hold quality" (how cleanly price respected the support cluster without violating it intrabar). Sums to 100 (breakout's 99 and dip-buy's 100 are each the result of independently tuning per-component weights for their setup, not a shared renormalization target — see `breakout/scoring.py`'s `DIP_BUY_WEIGHTS`):

| Component | Weight (v1) |
|---|---:|
| Support hold quality | 15 |
| Relative volume (on reversal bar) | 18 |
| Reversal candle quality | 20 |
| Trend (pre-existing uptrend strength) | 18 |
| Retest | 12 |
| Relative strength | 10 |
| Market confirmation | 5 |
| Sector confirmation | 2 |
| **Total** | **100** |

Interpretation unchanged from original doc for both setups: 90-100 A+, 80-89 A, 70-79 B, 60-69 C, <60 ignore.

## Hard rejection rules (override score, not just downgrade)

### Breakout

- Candle does not close above resistance → NOT_CONFIRMED
- RVOL below configured minimum → reject
- Severe upper rejection wick (>60% of range) → reject as fake-breakout risk
- Price closes back below breakout level same/next bar → invalidate

### Dip-buy

- Precondition uptrend not met (price below EMA50, or EMA20 below EMA50) → reject before pullback is even evaluated
- Pullback closes below the support cluster (not just wicks below it) → NOT_CONFIRMED, trend possibly broken
- Reversal candle without RVOL confirmation on that bar → reject
- Price makes a new low below the dip's low after CONFIRMED → invalidate (same "revisits its own low" rule as the stop-loss)

Market regime STRONG_BEAR/BEAR reduces score via a configurable multiplier for both setups (not a hard rejection) — though dip-buys in a bear regime should be treated with extra caution since "uptrend" preconditions are weaker signal in a broad downtrend.

## Backtest simulator — no-look-ahead by construction

The simulator walks the trading calendar (derived from dates actually present in `daily_ohlcv`, not an assumed Mon–Fri) day by day, symbol by symbol. Each day, each symbol's bar history is sliced up to and including "today" before being passed to the breakout engine — the code path structurally cannot read tomorrow's bar while deciding today's state transition.

Entry fills happen on the bar **after** the signal bar, never the signal bar itself. If next-day open gaps past the stop-loss, the trade is not filled — it's logged as `INVALIDATED_GAP` (not silently dropped, to avoid survivorship bias in the stats).

## Cost model

Slippage in basis points off next-day open, scaled by the symbol's ATR. Brokerage (flat or %, configurable — many discount brokers offer free/zero-brokerage delivery trades in India, which should be the default assumption here since this system only trades delivery). STT for delivery/equity trades is **0.1% on both buy and sell side** (not the 0.025% intraday sell-side-only rate the original planning doc assumed — that rate is wrong for this project since intraday is out of scope, see [Zero-Budget Constraint](#zero-budget-constraint)). Exact rate configurable and should be re-verified against current NSE/SEBI rules before trusting backtest P&L, but the default must be the delivery rate, not intraday's.

## Holding period (swing/delivery framing)

Every trade in this system is a delivery trade — bought and held across one or more days, never squared off same-day. This isn't a new constraint on top of the daily-bar design; it's what the daily-bar design already implies, made explicit here so nobody adds an intraday-exit assumption later:

- No same-day entry+exit. Entry fills next bar after signal (per [Backtest simulator](#backtest-simulator--no-look-ahead-by-construction) below); earliest possible exit is the bar after that.
- No minimum holding period is enforced by the engine — a trade can hit its stop-loss the day after entry. But target/SL levels are structural (ATR, swing points), not intraday levels, so realistic holding periods for a confirmed setup are expected to run days to a few weeks, not hours.
- No maximum holding period / time-stop in v1 — a trade stays TRADE_ACTIVE until TARGET_HIT, INVALIDATED (stop-loss), or SESSION_END (backtest window closes with the position still open, logged as-is rather than force-closed). A time-based exit rule is a candidate for the walk-forward optimization follow-up, not v1.

## Reproducibility

Every `backtest_results` row is tagged with a `strategy_version` string (a short git commit hash) so re-running after tuning `scoring.py` weights doesn't silently overwrite prior runs — needed to compare strategy versions against each other later.

## Future: Live Intraday Scanning

Not built in v1. Documented now because Angel One SmartAPI's likely-free WebSocket feed (unverified, see [Live Data: Angel One](#live-data-angel-one) above) would make it possible under the zero-budget constraint (unlike the originally-assumed paid-broker blocker) — but it is meaningfully new engineering, not a config flag on top of v1:

- A new candle-builder (tick → 1m/5m/15m candles) reintroducing the deferred `market_ticks`/`candles_1m/5m/15m` tables from [db.md](db.md) — currently marked budget-blocked there and would need to move to "planned."
- VWAP, ORB, and RVOL-by-time-bucket engines from the original planning doc (sections 21-22), none of which exist yet even as daily-bar approximations.
- The breakout/dip-buy state machines would need an intraday-aware variant, since the current design's no-look-ahead guarantee and bar-history slicing are built around one-bar-per-day granularity.
- A scrip-master lookup to resolve bare NSE tickers to Angel One's numeric `symboltoken`s (see the known v1 limitation in [Live Data: Angel One](#live-data-angel-one)) — needed at scale for any live scanner, not just the current single-symbol LTP overlay.
- Angel One's WebSocket connection (if confirmed available) would need to run inside a long-lived process during market hours — a different runtime shape than the current EOD-batch APScheduler design in [backend.md](backend.md).

**Recommended sequencing**: prove the daily-bar breakout/dip-buy engines with real backtest results first (v1-v3 per the build order below), *then* layer live scanning on top once there's confidence in the underlying signal logic — adding live data to an unvalidated strategy doesn't make the strategy better, it just makes losses happen faster. Live LTP display (already in v1, see above) is the only Angel One integration point until that validation exists.

## Known v1 limitations (carried forward from gap analysis)

- Daily bars only for signal detection — no intraday confirmation (VWAP, ORB, RVOL-by-time-bucket) yet; see [Future: Live Intraday Scanning](#future-live-intraday-scanning). Current price display *is* live (Angel One SmartAPI), the detection logic is not.
- Angel One LTP lookup requires a `symbol_token` the caller must supply manually — no scrip-master symbol-to-token lookup exists yet (see [Live Data: Angel One](#live-data-angel-one)).
- No news catalyst scoring (news engine not built yet)
- No walk-forward weight optimization — that's a separate follow-up spec, not in this slice
- No portfolio-level risk (correlation between simultaneous signals, max concurrent positions) — v1 backtests each signal independently
- Survivorship bias: universe pulled from current NSE listings only, not point-in-time historical listings
