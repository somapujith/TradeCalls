# Database

Status: design — schema not yet created. PostgreSQL via [Neon](https://neon.tech) (serverless Postgres), per [engine.md](engine.md) and the original planning doc's [section 11](../AI_Intraday_Breakout_Research_Planning.md#11-database-architecture).

## Provider: Neon

Connection via `DATABASE_URL` (Neon connection string, `sslmode=require`) in `backend/.env` — no local Postgres/docker-compose needed. Neon's branching feature is a good fit for this engine's `strategy_version` reproducibility requirement: a DB branch can be cut per backtest experiment to isolate schema/data changes during weight tuning without touching the main branch, then discarded or merged. Not required for v1, worth using once the walk-forward optimization loop (follow-up spec) starts iterating on scoring weights.

## v1 tables (daily-bar backtest slice)

| Table | Purpose |
|---|---|
| `stocks` | NSE symbol master (symbol, sector, listing status) |
| `daily_ohlcv` | Daily OHLCV per symbol, adjusted for splits/dividends |
| `technical_indicators` | EMA/SMA/RSI/MACD/ATR/Bollinger per symbol per date |
| `support_resistance_levels` | Computed levels + resistance clusters per symbol per date |
| `breakout_candidates` | Symbols currently in a non-terminal breakout state |
| `breakout_events` | State-machine transition log (append-only, one row per transition) |
| `trade_setups` | Entry/SL/targets/score generated on CONFIRMED, tagged `strategy_version` |
| `trade_outcomes` | MFE/MAE/target-hit/SL-hit/holding-days per closed `trade_setup` |
| `backtest_results` | Aggregated metrics per backtest run, keyed by `strategy_version` |

`backtest_results.strategy_version` (text) is the reproducibility key — every column that could change with a scoring-weight tweak or logic change must be traceable back to the version that produced it. Never overwrite a run in place; each version gets its own rows.

## Deferred tables (future intraday/live slice, not built in v1)

Per the original planning doc — `market_ticks`, `candles_1m`, `candles_5m`, `candles_15m`, `volume_profiles`, `news`, `corporate_announcements`, `market_sessions`, `telegram_alerts`, `model_decisions`. These require a live WebSocket feed and news source that don't exist yet — adding them now would be schema for code that can't run.

## Design notes

- Adjusted close only, everywhere — historical resistance levels break silently if raw and split-adjusted prices mix.
- No forward-filled bars — a missing day is a missing row, not a duplicated prior close. Downstream indicator/level code must handle gaps explicitly rather than assume daily continuity.
- `daily_ohlcv` is the source of truth for the trading calendar used by the backtest simulator (see engine.md) — the calendar is derived from dates actually present, not assumed Mon–Fri, so exchange holidays don't need a separate calendar table in v1.
