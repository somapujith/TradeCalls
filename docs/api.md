# API

Status: design — not yet implemented. Documents the planned REST surface of the FastAPI backend (`backend/app/`, per [engine.md](engine.md)'s module list — no `docs/backend.md` exists yet to supersede this) that a React/Vite frontend (no `docs/frontend.md` exists yet either) will call to read backtest results and, eventually, live data.

## Scope of v1

Only the daily-bar backtest engine exists in the design so far — no live scanner, no news engine, no Telegram integration (see [engine.md](engine.md#known-v1-limitations)). API v1 therefore only exposes read access to what [db.md](db.md)'s v1 tables actually hold: backtest runs, their aggregated metrics, the trade setups/outcomes that make up a run, and the computed universe/indicators used to generate them. Anything tied to live scanning, alerts, or news is out of scope for v1 — see [Future / v2](#future--v2-not-part-of-v1) below.

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

### `GET /api/backtests`
List known backtest runs, one row per distinct `strategy_version` present in `backtest_results`.

Response (array): `strategy_version`, `run_date`, `total_trades`, `win_rate`, `profit_factor` — enough to pick a run without fetching full metrics.

### `GET /api/backtests/{strategy_version}/metrics`
Aggregated metrics for one run, read from `backtest_results`. Field names follow `metrics.py`'s stated outputs in engine.md (win rate, profit factor, breakdowns by score bucket / sector / day-of-week / regime).

Response: `strategy_version`, `total_trades`, `win_rate`, `profit_factor`, `avg_mfe`, `avg_mae`, `avg_holding_days`, `breakdown_by_score_bucket`, `breakdown_by_sector`, `breakdown_by_day_of_week`, `breakdown_by_regime`.

404 if `strategy_version` has no `backtest_results` row.

### `GET /api/backtests/{strategy_version}/trades`
List `trade_setups` tagged with this `strategy_version`, each joined to its `trade_outcomes` row (a setup may not have closed yet within the backtest window, in which case `outcome` is `null`).

Response (array): `trade_setup_id`, `symbol`, `entry_date`, `entry_price`, `stop_loss`, `targets`, `score`, `outcome: { mfe, mae, target_hit, sl_hit, holding_days, exit_reason }`. `exit_reason` includes the `INVALIDATED_GAP` case documented in engine.md.

Supports `?symbol=` and `?score_min=` filters given `trade_setups` can run into the thousands per run.

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

- Live scanning / real-time breakout signals (needs a live broker feed, not built)
- WebSocket or polling endpoints over `market_ticks`, `candles_1m/5m/15m` (deferred tables, no live feed)
- Telegram alert delivery or `telegram_alerts` history (no Telegram integration yet)
- News/catalyst endpoints over `news`, `corporate_announcements` (no news engine yet)
- `model_decisions` / AI-assisted call endpoints (no such component designed yet)

Do not build against these until the corresponding engine/db design docs mark them in scope.

## Open questions

- Does `/api/backtests/{strategy_version}/trades` need pagination beyond `limit`/`offset` for very large runs (thousands of symbols × years of daily bars)?
- Should `/api/universe` support a `?historical=true` mode once point-in-time listings are available (db.md notes v1 universe has survivorship bias — current listings only)?
- Response envelope (bare array vs. `{data, meta}` wrapper) — not yet decided; this doc uses bare arrays as a placeholder.
