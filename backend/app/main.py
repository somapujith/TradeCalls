"""FastAPI app entrypoint — see docs/backend.md."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.backtest_runs import router as backtest_runs_router
from app.api.backtests import router as backtests_router
from app.api.calls import router as calls_router
from app.api.ltp import router as ltp_router
from app.api.news import router as news_router
from app.api.stocks import router as stocks_router
from app.api.universe import router as universe_router
from app.db.models import Base  # noqa: F401  (registers ORM tables on Base.metadata)
from app.db.session import Base as SessionBase, engine
from app.scheduler import shutdown_scheduler, start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FRONTEND_DEV_ORIGIN = "http://localhost:5173"
FRONTEND_PROD_ORIGIN = "https://trade-calls-lyart.vercel.app"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        SessionBase.metadata.create_all(bind=engine)
        logger.info("Database tables ensured via Base.metadata.create_all")
    except Exception:
        logger.exception(
            "Database unreachable at startup — continuing without ensuring tables. "
            "Set a working DATABASE_URL in .env; endpoints that hit the DB will fail until then."
        )

    start_scheduler()

    yield

    shutdown_scheduler()


app = FastAPI(title="TradeCalls API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_DEV_ORIGIN, FRONTEND_PROD_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(backtests_router)
app.include_router(backtest_runs_router)
app.include_router(calls_router)
app.include_router(ltp_router)
app.include_router(news_router)
app.include_router(stocks_router)
app.include_router(universe_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
