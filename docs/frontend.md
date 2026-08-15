# Frontend

Status: design — not yet implemented. This documents the agreed architecture for the v1 dashboard, a backtest-results viewer, and the future live-scanner UI it will grow into (see [AI_Intraday_Breakout_Research_Planning.md](../AI_Intraday_Breakout_Research_Planning.md) section 40 for the original live-dashboard mockup and section 47 for the stack this is drawn from).

## Scope of v1

[engine.md](engine.md) only produces daily-bar backtest data — no intraday candles, no live feed, no news. So v1 of the frontend is **not** the live intraday scanner from section 40. It's a backtest results/history viewer: score-bucket breakdowns, win rate, a trade list, and an equity curve, all read from `backtest_results` / `trade_setups` / `trade_outcomes` ([db.md](db.md)). The section 40 layout (live watchlist, breakout details, news panel, live chart) is future work, gated on intraday data and a live backend existing (see "Future work" below).

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Framework | React 18 | Function components + hooks only |
| Language | **JavaScript (JS), not TypeScript** | Explicit project constraint — `.jsx` files only, no `.tsx`, no `tsconfig.json` |
| Build tool | Vite | `npm create vite@latest -- --template react` (JS template, not `react-ts`) |
| Styling | Tailwind CSS | Utility-first, no separate CSS-in-JS library |
| Charting | Lightweight Charts | See below |
| Data fetching | `fetch` + polling (React Query optional later) | REST only in v1, see "Backend integration" |

### Lightweight Charts over Plotly

Section 47 lists both as options. Lightweight Charts (TradingView) is the v1 pick:

- Built for OHLC/candlestick + line-series financial charts specifically — Plotly is a general-purpose plotting library and carries a much larger bundle for that use case.
- Designed for high-frequency updates on a single series (append a bar, move a price line) without a full re-render — this matters once the dashboard moves from static backtest charts to a live-updating intraday chart in a future version.
- Small footprint (~45KB), no D3/general-plotting dependency tree to carry through Vite's bundle.

Plotly stays a reasonable fallback if a future page needs statistical chart types Lightweight Charts doesn't do (histograms of MFE/MAE distributions, scatter of score vs. outcome) — it can be added as a second, page-scoped dependency rather than the dashboard's primary chart engine.

## Directory structure

```text
frontend/
  index.html
  vite.config.js
  tailwind.config.js
  postcss.config.js
  package.json
  src/
    main.jsx
    App.jsx
    api/
      client.js            — fetch wrapper, base URL, error handling
      backtests.js          — GET /backtests, /backtests/:version/trades, /backtests/:version/metrics
    pages/
      BacktestResultsPage.jsx  — v1 landing page: score buckets, win rate, trade list, equity curve
      DashboardPage.jsx         — future: live scanner (section 40 layout), stubbed/hidden in v1
    components/
      backtest/
        ScoreBucketTable.jsx
        TradeList.jsx
        EquityCurveChart.jsx
        StrategyVersionPicker.jsx
      dashboard/                — future, built once intraday data exists
        MarketHeader.jsx
        BreakoutWatchlist.jsx
        BreakoutDetailsPanel.jsx
        NewsPanel.jsx
        ChartPanel.jsx
      common/
        Card.jsx
        StatusBadge.jsx
    hooks/
      usePolling.js          — generic interval-based refetch hook
    styles/
      index.css               — Tailwind directives + minimal globals
```

No `.tsx` anywhere in the tree — every component file is `.jsx`.

## v1 components (backtest results viewer)

| Component | Reads from | Purpose |
|---|---|---|
| `BacktestResultsPage` | — | Page shell: strategy version picker + the four views below |
| `StrategyVersionPicker` | `backtest_results.strategy_version` | Select which backtest run to view (per engine.md's reproducibility model, each `strategy_version` is a separate row set) |
| `ScoreBucketTable` | `backtest_results` metrics | Win rate / profit factor broken down by score bucket (90-100, 80-89, ...), sector, day-of-week, regime — mirrors `metrics.py` breakdowns |
| `TradeList` | `trade_setups` + `trade_outcomes` | Sortable/filterable table: symbol, entry, SL, targets, score, MFE/MAE, target/SL hit, holding days |
| `EquityCurveChart` | `trade_outcomes` (derived) | Lightweight Charts line series — cumulative P&L over the backtest's trading calendar |

## Future work — live scanner dashboard (section 40)

Deferred until intraday data (1m/5m candles, VWAP, live feed) and a live backend exist:

| Component | Section 40 role |
|---|---|
| `MarketHeader` | NIFTY / BANKNIFTY / VIX / market regime strip, "LIVE" indicator |
| `BreakoutWatchlist` | Live list of symbols by breakout state (WATCH / APPROACHING / CONFIRMED, etc. per engine.md's state machine) with score, price, direction |
| `BreakoutDetailsPanel` | Resistance, entry, VWAP, RVOL, candle quality for the selected watchlist row |
| `NewsPanel` | Corporate announcement, sentiment, severity, catalyst — blocked on the news engine (not built; see engine.md's known v1 limitations) |
| `ChartPanel` | Price / VWAP / EMA / resistance / breakout / targets overlay, live-updating |

These are named and scoped now so v1's directory layout doesn't need restructuring later — they stay unbuilt (or built as inert placeholders behind a feature flag) until their data sources exist.

## Backend integration

No `docs/backend.md` exists yet. Per [engine.md](engine.md) and section 47, the backend is a Python/FastAPI service; the frontend talks to it as follows:

- **v1: REST polling.** The frontend calls REST endpoints (e.g. `GET /api/backtests`, `GET /api/backtests/{strategy_version}/trades`) via `fetch`. Backtest results are static once a run completes, so polling is only needed for "is a new run available" checks, not live ticks — a coarse interval (e.g. 30-60s) via `usePolling` is enough, or a manual refresh button.
- **Future: WebSocket.** Once intraday data and a live backend exist (per the section 40 dashboard and section 48's V1 live-data plan in the original research doc), `BreakoutWatchlist` and `ChartPanel` will need push updates rather than polling — a WebSocket connection from FastAPI is the natural upgrade path. Not implemented now; REST polling would be both wasteful and too slow for tick-level updates, so this is explicitly deferred rather than half-built.

## Known v1 limitations

- No live data, no WebSocket — read-only historical viewer.
- No news panel — no news source exists yet (engine.md).
- `DashboardPage` and the `dashboard/` components are placeholders, not wired to real data.
- Single-user, no auth — matches the rest of the stack's local/single-machine deployment (section 47).
