from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import dispose_engines

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.alerts.evaluator import AlertEvaluator
    from app.workers.forecast_worker import ForecastWorker
    from app.workers.insights_worker import InsightsWorker
    from app.workers.report_worker import ReportWorker

    evaluator_task = asyncio.create_task(AlertEvaluator().run())
    forecast_task = asyncio.create_task(ForecastWorker().run())
    insights_task = asyncio.create_task(InsightsWorker().run())
    report_task = asyncio.create_task(ReportWorker().run())
    yield
    evaluator_task.cancel()
    forecast_task.cancel()
    insights_task.cancel()
    report_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await evaluator_task
    with contextlib.suppress(asyncio.CancelledError):
        await forecast_task
    with contextlib.suppress(asyncio.CancelledError):
        await insights_task
    with contextlib.suppress(asyncio.CancelledError):
        await report_task

    # Without this, `uvicorn --reload` leaks an asyncpg pool on every restart and
    # pytest emits unclosed-connection warnings.
    await dispose_engines()
    from app.redis import close_redis

    await close_redis()


def create_app() -> FastAPI:
    """Application factory.

    A factory rather than a module-level singleton so tests can rebuild the app
    after overriding the (lru_cached) settings.

    Routes are mounted WITHOUT an `/api` prefix. The Vite dev proxy rewrites
    `/api/*` to `/*`, so the browser calls `/api/auth/login` and FastAPI serves
    `/auth/login`. Any production reverse proxy must strip `/api` the same way.
    """
    settings = get_settings()

    # The interactive docs enumerate every endpoint and schema. Useful in
    # development, free reconnaissance in production.
    is_prod = settings.environment == "prod"

    app = FastAPI(
        title="Sentinel API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api.routes.alerts import router as alerts_router
    from app.api.routes.auth import router as auth_router
    from app.api.routes.devices import router as devices_router
    from app.api.routes.downloads import router as downloads_router
    from app.api.routes.fleet import router as fleet_router
    from app.api.routes.forecasts import router as forecasts_router
    from app.api.routes.incidents import router as incidents_router
    from app.api.routes.live import router as live_router
    from app.api.routes.notifications import router as notifications_router
    from app.api.routes.reports import router as reports_router
    from app.api.routes.series import router as series_router
    from app.ingest.ws import router as ingest_router
    from app.live.viewer_ws import router as viewer_router

    app.include_router(auth_router)
    app.include_router(devices_router)
    app.include_router(fleet_router)
    app.include_router(series_router)
    app.include_router(live_router)
    app.include_router(alerts_router)
    app.include_router(forecasts_router)
    app.include_router(incidents_router)
    app.include_router(notifications_router)
    app.include_router(reports_router)
    app.include_router(downloads_router)
    app.include_router(ingest_router)
    app.include_router(viewer_router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
