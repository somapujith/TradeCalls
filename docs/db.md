# Database

Status: design — schema not yet created. PostgreSQL via [Neon](https://neon.tech) (serverless Postgres, **free tier**), per [engine.md](engine.md) and the original planning doc's [section 11](../AI_Intraday_Breakout_Research_Planning.md#11-database-architecture). See engine.md's [Zero-Budget Constraint](engine.md#zero-budget-constraint) — this whole system runs on free infrastructure only; Neon's free tier (0.5GB storage) is the intended ceiling for v1's daily-bar/delivery-only data volume.

## Provider: Neon

Connection via `DATABASE_URL` (Neon connection string, `sslmode=require`) in `backend/.env` — no local Postgres/docker-compose needed. Neon's branching feature is a good fit for this engine's `strategy_version` reproducibility requirement: a DB branch can be cut per backtest experiment to isolate schema/data changes during weight tuning without touching the main branch, then discarded or merged (branching is included on Neon's free tier). Not required for v1, worth using once the walk-forward optimization loop (follow-up spec) starts iterating on scoring weights.

## v1 tables (daily-bar backtest slice)

| Table | Purpose |
|---|---|
| `stocks` | NSE symbol master (symbol, sector, listing status) |
| `daily_ohlcv` | Daily OHLCV per symbol, adjusted for splits/dividends |
| `technical_indicators` | EMA/SMA/RSI/MACD/ATR/Bollinger per symbol per date |
| `support_resistance_levels` | Computed levels + resistance clusters per symbol per date |
| `breakout_candidates` | Symbols currently in a non-terminal state, either setup type |
| `breakout_events` | State-machine transition log (append-only, one row per transition), either setup type |
| `trade_setups` | Entry/SL/targets/score generated on CONFIRMED, tagged `strategy_version` and `setup_type` |
| `trade_outcomes` | MFE/MAE/target-hit/SL-hit/holding-days per closed `trade_setup` |
| `backtest_results` | Aggregated metrics per backtest run, keyed by `strategy_version` |

`trade_setups.setup_type` (text: `BREAKOUT` or `DIP_BUY`) discriminates the two setup detectors documented in [engine.md](engine.md#setup-types) — same table, same columns, different scoring/rejection logic upstream. `breakout_candidates` and `breakout_events` carry the same discriminator since both state machines share the underlying tables (the name `breakout_candidates` predates the dip-buy addition and is kept as-is rather than renamed, to avoid a disruptive rename of an already-referenced table).

`backtest_results.strategy_version` (text) is the reproducibility key — every column that could change with a scoring-weight tweak or logic change must be traceable back to the version that produced it. Never overwrite a run in place; each version gets its own rows. Metrics should be breakable down by `setup_type` in addition to the existing score-bucket/sector/day-of-week/regime dimensions (per engine.md), since breakout and dip-buy are expected to have different win-rate/profit-factor characteristics and conflating them would hide that.

## Deferred tables (future intraday/live slice — not built in v1)

Per the original planning doc — `market_ticks`, `candles_1m`, `candles_5m`, `candles_15m`, `volume_profiles`, `market_sessions`. These require a live tick feed, candle-builder, and intraday-aware breakout/dip-buy engines, none of which are built yet — see [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning). No longer necessarily budget-blocked (Angel One SmartAPI's likely-free WebSocket feed may remove that wall — unverified, see [engine.md](engine.md#live-data-angel-one)), but still genuinely deferred pending the daily-bar engines proving out first per engine.md's recommended sequencing. Do not build these until that design doc marks live scanning in scope.

`news`, `corporate_announcements`, `telegram_alerts`, `model_decisions` remain deferred for the same reason — free paths exist (unofficial NSE scraping, free RSS, free Telegram Bot API) but the news/AI engines that would populate them aren't built yet. See engine.md's news-engine scope note.

## Future: paper-trading tables (v5, not v1)

Per the original planning doc's section 52 (Paper Trading) — not designed in detail yet, but anticipated shape: a `paper_positions` table (symbol, entry date/price, SL, targets, `strategy_version`, status) distinct from `trade_setups`/`trade_outcomes` so live paper-trading state doesn't get mixed with historical backtest replay rows. Deferred until v1-v3 (engine + backtest + validation) are working — see engine.md's V1-V5 build order.

## Design notes

- Adjusted close only, everywhere — historical resistance levels break silently if raw and split-adjusted prices mix.
- No forward-filled bars — a missing day is a missing row, not a duplicated prior close. Downstream indicator/level code must handle gaps explicitly rather than assume daily continuity.
- `daily_ohlcv` is the source of truth for the trading calendar used by the backtest simulator (see engine.md) — the calendar is derived from dates actually present, not assumed Mon–Fri, so exchange holidays don't need a separate calendar table in v1.
