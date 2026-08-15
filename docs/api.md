# API

Status: design — not yet implemented. Documents the planned REST surface of the FastAPI backend ([backend.md](backend.md), `backend/app/`) that a React/Vite frontend ([frontend.md](frontend.md)) will call to read backtest results.

## Scope of v1

The daily-bar backtest engine covers two setup types — breakout and dip-buy (see [engine.md](engine.md#setup-types)) — no news engine, no Telegram integration (see [engine.md](engine.md#known-v1-limitations)). Live *signal detection* (intraday candles, VWAP, ORB) is deferred, not permanently out of scope — Kotak Neo's free WebSocket feed removed the earlier budget blocker, but the engine work to use it doesn't exist yet (see [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning)). Live *price display* (LTP) is in scope now via Kotak Neo, display-only, not feeding any engine (see [engine.md](engine.md#live-data-kotak-neo)). API v1 exposes read access to [db.md](db.md)'s v1 tables — backtest runs, their aggregated metrics, the trade setups/outcomes that make up a run, the computed universe/indicators used to generate them — plus current-price lookup. Anything tied to live signal detection, alerts, or news is out of scope for v1 — see [Future / v2](#future--v2-not-part-of-v1) below.

## Conventions

- Base path: `/api`. JSON in, JSON out.
- `strategy_version` (text, per [db.md](db.md)) is the primary key across all backtest-related endpoints. It is always a required path parameter — never defaulted to "latest" — so a client can't silently mix rows from two tuning runs.
- List endpoints accept `limit`/`offset` query params; default `limit` is small (e.g. 50).
- Errors are plain `{detail: str}`, no stack traces or internal paths (per repo security conventions).
- No auth in v1 — this is a local/single-user tool per the current design. Revisit before any multi-user or public deployment.

## v1 Endpoints

| Method + path | Purpose | Backed by |
|---|---|---|
| `GET /api/backtests` | List known runs | `backtest_results` (distinct `strategy_version`) |
| `GET /api/backtests/{strategy_version}/metrics` | Aggregated metrics for one run | `backtest_results` |
| `GET /api/backtests/{strategy_version}/trades` | Trade setups + outcomes for one run | `trade_setups` join `trade_outcomes` |
| `GET /api/universe` | Current liquidity-filtered universe | `stocks` + `daily_ohlcv` (computed, not stored) |
| `GET /api/stocks/{symbol}/indicators` | Indicator history for a symbol | `technical_indicators` |
| `GET /api/calls` | Active buy calls for the dashboard — symbol, LTP, reason, confidence | `trade_setups` (non-terminal) + `breakout_events` + live `kotak_neo_client` LTP |
| `GET /api/ltp/{symbol}` | Current price for one symbol | `kotak_neo_client.py` (see engine.md#live-data-kotak-neo), not a stored table |

### `GET /api/backtests`
List known backtest runs, one row per distinct `strategy_version` present in `backtest_results`.

Response (array): `strategy_version`, `run_date`, `total_trades`, `win_rate`, `profit_factor` — enough to pick a run without fetching full metrics.

### `GET /api/backtests/{strategy_version}/metrics`
Aggregated metrics for one run, read from `backtest_results`. Field names follow `metrics.py`'s stated outputs in engine.md (win rate, profit factor, breakdowns by score bucket / sector / day-of-week / regime).

Response: `strategy_version`, `total_trades`, `win_rate`, `profit_factor`, `avg_mfe`, `avg_mae`, `avg_holding_days`, `breakdown_by_score_bucket`, `breakdown_by_sector`, `breakdown_by_day_of_week`, `breakdown_by_regime`.

404 if `strategy_version` has no `backtest_results` row.

### `GET /api/backtests/{strategy_version}/trades`
List `trade_setups` tagged with this `strategy_version`, each joined to its `trade_outcomes` row (a setup may not have closed yet within the backtest window, in which case `outcome` is `null`).

Response (array): `trade_setup_id`, `symbol`, `setup_type`, `entry_date`, `entry_price`, `stop_loss`, `targets`, `score`, `outcome: { mfe, mae, target_hit, sl_hit, holding_days, exit_reason }`. `setup_type` is `BREAKOUT` or `DIP_BUY` (see [engine.md](engine.md#setup-types)). `exit_reason` includes the `INVALIDATED_GAP` case documented in engine.md.

Supports `?symbol=`, `?score_min=`, and `?setup_type=` filters given `trade_setups` can run into the thousands per run.

### `GET /api/calls`
The dashboard's primary view — active (non-terminal-state) buy calls, i.e. `trade_setups` rows whose `breakout_events` state is not one of `TARGET_HIT`/`INVALIDATED`/`SESSION_END`, from the **latest** `strategy_version` (this endpoint intentionally does not take a `strategy_version` path param — it always reflects the current production run, unlike the backtest-comparison endpoints above which are explicitly per-version).

Response (array): `symbol`, `setup_type` (`BREAKOUT`/`DIP_BUY`), `state` (current state-machine value, e.g. `CONFIRMED`/`RETEST_PENDING`/`TRADE_ACTIVE`), `entry_price`, `stop_loss`, `targets`, `confidence` (the setup's `score`, 0-100 — see [engine.md's Scoring](engine.md#scoring); v1 confidence *is* this score, not a separately-trained model, see [Future / v2](#future--v2-not-part-of-v1)), `reason` (a short structured breakdown of which score components fired — e.g. which of resistance-breakout/RVOL/candle-quality/trend/retest/relative-strength/market/sector contributed, not free text from an LLM since no LLM exists in v1), `ltp` (from `kotak_neo_client`, `null` if the live lookup fails — a stale/missing LTP must not block the call from showing, since the call itself comes from yesterday's close, not from LTP).

`ltp` is fetched live per request (or from a short-TTL cache, e.g. 60s, to avoid hammering Kotak Neo on every dashboard poll) — it is not stored in `trade_setups`, which stays a pure historical/backtest table per db.md.

### `GET /api/ltp/{symbol}`
Current price for one symbol via `kotak_neo_client.get_ltp()`. Used by the frontend to refresh price independently of the full `/api/calls` payload (e.g. a per-row refresh button).

Response: `symbol`, `price`, `timestamp`. 502 (not 404) if the Kotak Neo session/API call fails — the symbol may be valid but the live lookup unavailable; the frontend should treat this as "LTP unavailable," not "symbol doesn't exist."

### `GET /api/universe`
Browse the liquidity-filtered universe as `universe.py` would compute it (price > ₹50, 20D avg turnover > ₹10Cr, excludes illiquid/suspended) — **computed on request from `stocks` + `daily_ohlcv`, not a stored table** (db.md has no universe snapshot table in v1).

Query params: `as_of_date` (optional, defaults to latest date present in `daily_ohlcv`; historical universe reconstruction is only as accurate as the filter re-run against that date's data).

Response (array): `symbol`, `sector`, `listing_status`, `close`, `avg_turnover_20d`.

### `GET /api/stocks/{symbol}/indicators`
Computed technical indicators for one symbol over a date range, read from `technical_indicators`.

Query params: `start_date`, `end_date` (both required — this table is per-symbol-per-date and can be large).

Response (array, one row per date): `date`, `ema9`, `ema20`, `ema50`, `sma100`, `sma200`, `rsi`, `macd`, `macd_signal`, `atr`, `bb_upper`, `bb_lower`.

404 if `symbol` isn't in `stocks`; empty array (not 404) if the symbol exists but has no indicator rows in range.

## Not yet exposed (v1 tables without an endpoint)

`support_resistance_levels`, `breakout_candidates`, and `breakout_events` exist in [db.md](db.md)'s v1 schema but have no endpoint above — deferred until the frontend has a concrete view that needs them (e.g. a per-symbol breakout-state timeline), so as not to invent response shapes nobody consumes yet.

## Future / v2 (not part of v1)

The following are explicitly **out of scope** for this API until the underlying engine components exist (see engine.md's "Known v1 limitations" and db.md's "Deferred tables"):

- Live scanning / real-time breakout signal *detection* (Kotak Neo's WebSocket feed is available, but the candle-builder/VWAP/ORB engines it would feed aren't built — see [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning); LTP *display* is already in v1 via `/api/ltp` and `/api/calls`, this bullet is about detection, not price)
- WebSocket or polling endpoints over `market_ticks`, `candles_1m/5m/15m` (deferred tables, no candle-builder yet)
- Telegram alert delivery or `telegram_alerts` history (no Telegram integration yet)
- News/catalyst endpoints over `news`, `corporate_announcements` (no news engine yet)
- ML-trained confidence — v1's `confidence` field on `/api/calls` is the deterministic scoring-engine output (engine.md's weighted score), not a trained model's prediction. A separately trained confidence model (gradient boosting on `trade_outcomes`, per the original planning doc's [section 43](../AI_Intraday_Breakout_Research_Planning.md#43-machine-learning--ai-improvement)) needs enough closed-trade backtest history to train on first — not viable before v1's backtest engine has run and accumulated outcomes. Design deferred to a follow-up spec once that data exists; `model_decisions` table stays unbuilt until then.

Do not build against these until the corresponding engine/db design docs mark them in scope.

## Open questions

- Does `/api/backtests/{strategy_version}/trades` need pagination beyond `limit`/`offset` for very large runs (thousands of symbols × years of daily bars)?
- Should `/api/universe` support a `?historical=true` mode once point-in-time listings are available (db.md notes v1 universe has survivorship bias — current listings only)?
- Response envelope (bare array vs. `{data, meta}` wrapper) — not yet decided; this doc uses bare arrays as a placeholder.
