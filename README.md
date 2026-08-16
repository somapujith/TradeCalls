# TradeCalls

Daily-bar breakout + dip-buy backtest engine, FastAPI backend, React dashboard. Zero paid dependencies — see [docs/engine.md](docs/engine.md#zero-budget-constraint). Full design docs in [docs/](docs/); this file is just "how do I run it."

## Status (as of 2026-08-16, overnight auto-build)

- **DB**: real Neon Postgres, schema applied via Alembic (`3a2d9f6358a6_initial_v1_schema`), verified zero drift against `backend/app/db/models.py`.
- **Backend**: FastAPI + APScheduler, all v1 endpoints from [docs/api.md](docs/api.md) wired, 555 tests passing, ~86% coverage.
- **Frontend**: React/Vite/Tailwind, `CallsPage` + `BacktestResultsPage` built per [docs/frontend.md](docs/frontend.md), `npm run build` clean.
- **Placeholders — need real credentials before these work**: Angel One SmartAPI (`ANGEL_ONE_*` in `backend/.env`, LTP display only) and the optional news module (`NEWS_API_NEWSAPI_KEY` / `NEWS_API_GNEWS_KEY`). Nothing else in the engine depends on these — backtesting works fully without them. Even with credentials filled in, `/api/ltp` currently requires a `symbol_token` per symbol — v1 has no scrip-master lookup yet (see [docs/engine.md](docs/engine.md#live-data-angel-one)).

## Run it

Backend:
```bash
cd backend
python -m venv .venv
./.venv/Scripts/pip install -r requirements.txt   # Windows; use bin/pip on Mac/Linux
cp .env.example .env   # then fill in DATABASE_URL (Neon) at minimum
./.venv/Scripts/python -m alembic upgrade head
./.venv/Scripts/python -m uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

Tests:
```bash
cd backend
./.venv/Scripts/python -m pytest
```

## Notes for whoever picks this up next

- `backend/.env` is gitignored and already has the real `DATABASE_URL` for this Neon project — don't overwrite it with the `.env.example` placeholder.
- `pandas-ta` (pinned in the original docs) isn't installable on Python 3.11 via PyPI — indicators are implemented directly with pandas/numpy instead, see the comment in `backend/requirements.txt`.
- `pydantic`/`pydantic-settings`/`fastapi`/`alembic` versions in `requirements.txt` are a verified-working matrix — some originally-pinned versions had broken internal imports (see comments inline). Don't bump individual packages without checking the whole set still imports.
- This project originally integrated Kotak Neo for live LTP, but Kotak's portal never exposed a usable consumer secret — replaced with Angel One SmartAPI (`smartapi-python` + `pyotp` for TOTP login). `app/data/angel_one_client.py` imports the SDK lazily and only raises at call-time, so the app boots fine even without credentials or the package installed.
