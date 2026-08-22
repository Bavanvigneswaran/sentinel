from __future__ import annotations

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
    yield
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

    from app.api.routes.auth import router as auth_router
    from app.api.routes.devices import router as devices_router
    from app.api.routes.fleet import router as fleet_router
    from app.api.routes.live import router as live_router
    from app.api.routes.series import router as series_router
    from app.ingest.ws import router as ingest_router
    from app.live.viewer_ws import router as viewer_router

    app.include_router(auth_router)
    app.include_router(devices_router)
    app.include_router(fleet_router)
    app.include_router(series_router)
    app.include_router(live_router)
    app.include_router(ingest_router)
    app.include_router(viewer_router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
