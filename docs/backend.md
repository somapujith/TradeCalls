# Backend

Status: design — not yet implemented. This documents the FastAPI/scheduler layer that will host the engine and database described in [engine.md](engine.md) and [db.md](db.md), per the original planning doc's [section 46](../AI_Intraday_Breakout_Research_Planning.md#46-suggested-project-structure) and [section 47](../AI_Intraday_Breakout_Research_Planning.md#47-recommended-technology-stack). All components below are free/open-source — see engine.md's [Zero-Budget Constraint](engine.md#zero-budget-constraint).

## Scope of v1

The backend is a thin host for the daily-bar backtest engine — a serving layer plus a scheduler that runs the engine's modules in order, for **both** setup types (breakout and dip-buy, see [engine.md](engine.md#setup-types)) — plus a live LTP lookup via Angel One SmartAPI for dashboard display (see [engine.md](engine.md#live-data-angel-one)). It does not do live/intraday *signal detection* yet (deferred, not permanently blocked — see [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning)), does not run an LLM/SLM, and does not send alerts; SLM/LLM/Telegram are v2+ (see Future work below), and remain free-tier/local-only when built (LM Studio runs local models at no cost; Telegram Bot API is free).

## Directory structure

```
backend/
  app/
    main.py            — FastAPI app entrypoint: wires routers, starts the scheduler on startup, sets up the DB connection pool
    config.py           — Pydantic settings: DB URL, universe thresholds (₹50 price floor, ₹10Cr turnover), cost model params, scheduler cadence
    scheduler.py         — APScheduler job definitions and registration (see below)

    data/
      yfinance_client.py — daily OHLCV per symbol, adjusted close (see engine.md)
      universe.py          — liquidity filter (see engine.md)
      angel_one_client.py  — live LTP for dashboard display only, not fed into engines (see engine.md#live-data-angel-one)

    market/
      indicators.py        — EMA/SMA/RSI/MACD/ATR/Bollinger (see engine.md)
      levels.py              — support/resistance levels (see engine.md)
      relative_strength.py   — stock vs sector vs NIFTY (see engine.md)
      market_regime.py       — market regime classification (see engine.md)

    breakout/
      breakout_engine.py     — breakout state machine (see engine.md)
      dip_buy_engine.py        — dip-buy state machine (see engine.md#dip-buy-setup)
      scoring.py               — breakout + dip-buy scores, hard rejection rules for both (see engine.md)

    backtest/
      simulator.py            — event loop replay (see engine.md)
      execution.py             — fills, slippage, brokerage, STT (see engine.md)
      outcomes.py               — MFE/MAE/target-hit/SL-hit (see engine.md)
      metrics.py                 — win rate, profit factor, breakdowns (see engine.md)

  tests/
```

`data/`, `market/`, `breakout/`, `backtest/` are the engine — engine.md is authoritative for what they do internally. This doc only covers `main.py`, `config.py`, `scheduler.py`, and how the app around them is structured. `ai/`, `trading/`, `notifications/` from the original planning doc's [section 46](../AI_Intraday_Breakout_Research_Planning.md#46-suggested-project-structure) are deliberately absent — they belong to v2+ (SLM/LLM, live entry/SL/target execution, Telegram) and adding empty stubs now would be scaffolding for code that can't run yet.

## FastAPI's role

FastAPI is a serving layer, not a computation layer. It:

- Exposes read endpoints over the Postgres tables from db.md — `breakout_candidates`, `breakout_events`, `trade_setups`, `trade_outcomes`, `backtest_results` — for the future dashboard (planning doc [section 40](../AI_Intraday_Breakout_Research_Planning.md#40-dashboard)) to consume.
- Exposes a trigger endpoint to kick off a backtest run (`backtest/simulator.py`) as a background task, since a full historical replay is too slow to run inline in a request.
- Does not compute indicators, run the breakout state machine, or score anything itself — that logic lives in `market/`, `breakout/`, `backtest/` and is invoked by the scheduler or the background task above. FastAPI only reads and writes rows.
- No auth in v1 — single-user, runs locally. Revisit before this is ever exposed off localhost.

Indicative v1 endpoint shape (illustrative, not a committed contract):

| Endpoint | Method | Purpose |
|---|---|---|
| `/breakout-candidates` | GET | Symbols currently in a non-terminal breakout or dip-buy state |
| `/trade-setups/{symbol}` | GET | Entry/SL/targets/score history for a symbol |
| `/backtest-results?strategy_version=...` | GET | Aggregated metrics for a given `strategy_version` (see engine.md Reproducibility) |
| `/backtest-runs` | POST | Trigger a new `backtest/simulator.py` run as a background task; response includes the `strategy_version` it will be tagged with |
| `/ltp/{symbol}` | GET | Current price via `angel_one_client.py` (see engine.md#live-data-angel-one) — display-only, not used by any engine logic |

## config.py

Pydantic-based settings (per planning doc [section 47](../AI_Intraday_Breakout_Research_Planning.md#47-recommended-technology-stack)), loaded from environment variables — no hardcoded secrets:

| Setting group | Examples |
|---|---|
| Database | connection string |
| Universe filter | price floor, turnover floor (see db.md/engine.md) |
| Cost model | slippage bps, brokerage, STT rate |
| Scheduler | job cadence, timezone |
| Data client | yfinance retry/backoff, request rate limit |
| Angel One | `angel_one_api_key`/`angel_one_client_code`/`angel_one_mpin`/`angel_one_totp_secret` — never hardcoded, loaded from `.env` (see engine.md#live-data-angel-one) |

## Scheduler (APScheduler)

v1 is an offline batch system — there is no market open to scan intraday yet, since only daily bars exist. The scheduler runs one EOD job chain instead of the live loop:

| Job | Trigger | Does |
|---|---|---|
| EOD ingestion | Daily, after NSE close settles (yfinance daily bars available) | `data/yfinance_client.py` pulls the day's OHLCV for the universe; `data/universe.py` refreshes the liquidity filter |
| Derived data refresh | Immediately after ingestion | `market/indicators.py`, `market/levels.py`, `market/relative_strength.py`, `market/market_regime.py` recompute for the new day, truncated to "today" per engine.md's no-look-ahead rule |
| Breakout state advance | Immediately after derived data refresh | `breakout/breakout_engine.py` advances each symbol's state on the new bar; `breakout/scoring.py` scores CONFIRMED signals; new `trade_setups` rows persisted |
| Backtest run | On-demand via the FastAPI trigger endpoint, not cron | `backtest/simulator.py` replays a date range for strategy tuning/comparison — not part of the daily production path in v1 |

This collapses planning doc [section 45](../AI_Intraday_Breakout_Research_Planning.md#45-suggested-daily-scheduler)'s 08:30–09:00 pre-market block and its 09:15–15:30 live breakout scanner (1-minute candle updates, 5-minute breakout scans, SLM/LLM triggers, Telegram alerts) into a single EOD batch chain, because v1 has no candle-builder, no pre-market data source, and no news engine — a live *price* feed exists (Angel One SmartAPI, used for on-demand LTP only, see engine.md), but nothing in the scheduler consumes it yet. The full intraday timeline in section 45 is the target once the detection engines exist — it is not built here.

Exact trigger times are configurable in `config.py`, not hardcoded, per planning doc section 45's closing note.

## Deployment

Per planning doc [section 47](../AI_Intraday_Breakout_Research_Planning.md#47-recommended-technology-stack), v1 targets a single local machine for the Python services (FastAPI + APScheduler in one process). Database is [Neon](https://neon.tech) (serverless Postgres, see [db.md](db.md)) rather than a local instance — no docker-compose/local Postgres needed, backend connects via `DATABASE_URL` in `.env`. Frontend is designed in [frontend.md](frontend.md) (React + Vite + JS) but not yet built. No containerization or multi-host deployment is designed yet; revisit if/when this needs to run unattended off a single laptop.

## Known v1 limitations

- No live scanning loop — the scheduler only runs an EOD batch chain (see above).
- No background job framework beyond APScheduler's in-process jobs — no retry/dead-letter handling for a failed EOD ingestion beyond what APScheduler itself provides.
- No auth, no rate limiting on the API — acceptable only because this runs on localhost for a single user in v1.
- Backtest runs are triggered synchronously by an operator, not scheduled — there is no automatic nightly re-run of the full backtest suite in v1.

## Future work (v2+, out of scope for detail here)

- Live intraday scanner loop (09:15–15:30, per-minute candle/indicator updates, 5-minute breakout scans) — Angel One SmartAPI's WebSocket feed (if confirmed available, see engine.md's Live Data section) would remove the earlier budget blocker, but the candle-builder, VWAP/ORB engines, a scrip-master token lookup, and a long-lived market-hours process are all unbuilt; see [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning) for sequencing (daily-bar engines validate first).
- SLM/LLM candidate explanation (`ai/` module, planning doc section 46).
- Telegram alerting (`notifications/` module).
- A `trading/` module for live entry/SL/target execution — v1's `breakout_engine.py` already computes these values for backtesting per engine.md, but wiring them to a live/paper-trading path is separate work.
- Auth, once/if the backend is exposed beyond localhost.
