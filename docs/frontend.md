# Frontend

Status: design — not yet implemented. This documents the agreed architecture for the v1 dashboard, a backtest-results viewer, and the future live-scanner UI it will grow into (see [AI_Intraday_Breakout_Research_Planning.md](../AI_Intraday_Breakout_Research_Planning.md) section 40 for the original live-dashboard mockup and section 47 for the stack this is drawn from).

## Scope of v1

[engine.md](engine.md) produces daily-bar backtest data for two setup types — breakout and dip-buy — plus live current-price lookup via Kotak Neo (display-only, see [engine.md](engine.md#live-data-kotak-neo)). No intraday candles, no live signal detection, no news yet — so v1 of the frontend is **not** the full live intraday scanner from section 40 (that's gated on the candle-builder/VWAP/ORB engines not existing yet, see [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning), not on budget anymore).

v1 has **two pages**:

1. **`CallsPage`** (default landing page) — the active buy-calls dashboard: every open call (breakout or dip-buy) with its current LTP, the reason it fired, and its confidence score. This is the page for "what should I be looking at right now."
2. **`BacktestResultsPage`** — score-bucket breakdowns, win rate, a trade list (filterable by setup type), and an equity curve, all read from historical `backtest_results` / `trade_setups` / `trade_outcomes` ([db.md](db.md)). This is the page for "does this strategy actually work."

The section 40 layout (market header, news panel, live chart overlay) stays documented below for completeness but is gated on the engine work in engine.md's Future section, not built yet.

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
      calls.js               — GET /calls, /ltp/:symbol
    pages/
      CallsPage.jsx             — v1 default landing page: active calls with LTP, reason, confidence
      BacktestResultsPage.jsx   — score buckets, win rate, trade list, equity curve
      DashboardPage.jsx         — future: live scanner (section 40 layout), stubbed/hidden in v1
    components/
      calls/
        CallCard.jsx             — one call: symbol, setup type badge, LTP, entry/SL/targets, confidence, reason
        ConfidenceBadge.jsx       — visual treatment for the 0-100 score (color bands matching engine.md's A+/A/B/C/ignore tiers)
        ReasonBreakdown.jsx       — expandable list of which score components fired (see api.md's /api/calls `reason` field)
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

## v1 components — Calls dashboard

| Component | Reads from | Purpose |
|---|---|---|
| `CallsPage` | `GET /api/calls` | Page shell: polls the active-calls list (e.g. every 60s via `usePolling`, matching `/api/calls`' LTP cache TTL per api.md) and renders one `CallCard` per open call |
| `CallCard` | one row of `/api/calls` | Symbol, `setup_type` badge (breakout/dip-buy — visually distinct, since they're opposite entry logic per engine.md), LTP (with a "last updated" timestamp, since it's polled not streamed), entry/SL/target ladder, `ConfidenceBadge`, expandable `ReasonBreakdown` |
| `ConfidenceBadge` | `confidence` (0-100) | Color-coded to engine.md's score interpretation: 90-100 A+ (strongest), 80-89 A, 70-79 B, 60-69 C, <60 not shown (calls below 60 shouldn't reach `/api/calls` per engine.md's hard-rejection/scoring model). Labeled "Confidence" not "Score" in the UI, per the intent behind this dashboard, but the underlying number is engine.md's deterministic weighted score — see [Confidence model](#confidence-model-v1-vs-future) below, this is not a trained ML prediction in v1. |
| `ReasonBreakdown` | `reason` (structured, from `/api/calls`) | Which score components fired and their weight contribution — e.g. for a breakout call: resistance breakout ✓17, RVOL ✓23, candle quality ✓11, trend ✓11, retest — pending, relative strength ✓11, market ✓6, sector ✓3. Mirrors engine.md's scoring table per setup type, not free-text explanation (no LLM in v1). |

## Confidence model: v1 vs. future

**v1: confidence = the scoring engine's output**, unmodified. `engine.md`'s weighted score (breakout or dip-buy table, renormalized to 100) *is* the confidence shown on `CallCard`. This is deterministic and reproducible — same inputs always produce the same confidence, tied to `strategy_version` like everything else in the backtest engine.

**Future: a trained confidence model** (not v1). The original planning doc's [section 43](../AI_Intraday_Breakout_Research_Planning.md#43-machine-learning--ai-improvement) describes training a model (candidates: LightGBM, XGBoost, Random Forest, logistic regression — all free/open-source, no licensing cost) on closed `trade_outcomes` to predict win probability from the same features that feed the deterministic score, then using *that* prediction as confidence instead of (or blended with) the hand-weighted score. This needs a meaningful number of closed backtest trades to train on — not viable until v1's backtest engine has actually run across enough history to produce them. Not designed in detail yet; when it is, it becomes a new backend component (`ai/confidence_model.py` or similar) that `/api/calls`' `confidence` field switches to reading from, without the frontend needing to change — `CallCard`/`ConfidenceBadge` just render whatever number `/api/calls` returns, so this is a backend-only upgrade path once built.

## Future work — live scanner dashboard (section 40)

Blocked on a paid live broker feed (see engine.md's Zero-Budget Constraint), not merely "not built yet." Documented here so the v1 directory layout doesn't need restructuring if the budget constraint is ever revisited, but there is no current plan to build this:

| Component | Section 40 role |
|---|---|
| `MarketHeader` | NIFTY / BANKNIFTY / VIX / market regime strip, "LIVE" indicator |
| `BreakoutWatchlist` | Live list of symbols by breakout state (WATCH / APPROACHING / CONFIRMED, etc. per engine.md's state machine) with score, price, direction |
| `BreakoutDetailsPanel` | Resistance, entry, VWAP, RVOL, candle quality for the selected watchlist row |
| `NewsPanel` | Corporate announcement, sentiment, severity, catalyst — blocked on the news engine (not built; see engine.md's known v1 limitations) |
| `ChartPanel` | Price / VWAP / EMA / resistance / breakout / targets overlay, live-updating |

These are named and scoped now so v1's directory layout doesn't need restructuring later — they stay unbuilt (or built as inert placeholders behind a feature flag) until their data sources exist.

## Backend integration

Per [backend.md](backend.md), the backend is a Python/FastAPI service; the frontend talks to it as follows:

- **v1: REST polling for everything, including LTP.** The frontend calls REST endpoints (`GET /api/calls`, `GET /api/ltp/:symbol`, `GET /api/backtests`, etc.) via `fetch`. `CallsPage` polls `/api/calls` on a coarse interval (e.g. 60s, matching the backend's LTP cache TTL per api.md) — this is polling a REST endpoint that itself does a live Kotak Neo lookup server-side, not a client-side WebSocket to Kotak Neo. Backtest results are static once a run completes, so `BacktestResultsPage` only needs polling for "is a new run available," or a manual refresh button.
- **Future: WebSocket for tick-level updates.** Once the candle-builder/live-detection engines exist (per [engine.md's Future: Live Intraday Scanning](engine.md#future-live-intraday-scanning)), `BreakoutWatchlist` and `ChartPanel` (section 40 layout) will need push updates rather than polling — a WebSocket connection from FastAPI is the natural upgrade path. Not implemented now; REST polling would be too slow for tick-level updates, so this is explicitly deferred rather than half-built. Note this is a separate need from `CallsPage`'s LTP polling above, which is adequately served by REST at a 60s cadence since v1 calls are daily-bar-generated, not tick-reactive.

## Known v1 limitations

- LTP is polled REST, not streamed — adequate for daily-bar-generated calls, not for a tick-reactive live scanner (that's the Future work section, separately gated).
- `confidence` is the deterministic scoring engine's output, not a trained ML model — see [Confidence model](#confidence-model-v1-vs-future).
- No news panel — no news source exists yet (engine.md).
- `DashboardPage` and the `dashboard/` components are placeholders, not wired to real data.
- Single-user, no auth — matches the rest of the stack's local/single-machine deployment (section 47).
