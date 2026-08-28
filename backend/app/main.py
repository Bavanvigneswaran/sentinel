from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from app.config import get_settings
from app.db import dispose_engines
from app.security.headers import SecurityHeadersMiddleware
from app.webapp import ApiPrefixMiddleware, WebConsoleMiddleware, resolve_dist

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

    # Mirrors what a production reverse proxy does, and what the Vite dev proxy
    # already does, so the console's `/api/...` calls reach routes mounted at
    # the root. See app/webapp.py and Phase 1's "routes carry no /api prefix".
    app.add_middleware(ApiPrefixMiddleware)

    # Must run BEFORE the router: `/devices` is both a REST endpoint and a page
    # in the console's client-side router, and a route registered on the app
    # would always win the match. See WebConsoleMiddleware for the full
    # reasoning. Added after ApiPrefixMiddleware so it runs first (Starlette
    # applies middleware in reverse registration order) and never sees a
    # rewritten path.
    if settings.serve_web_console:
        dist = resolve_dist(settings.web_dist_dir)
        if dist is None:
            logger.info(
                "web console not served: no build found (run `make web-build`). "
                "The API still serves only its own routes."
            )
        else:
            app.add_middleware(WebConsoleMiddleware, dist=dist)
            logger.info("serving the web console from %s", dist)

    # Added second-to-last, so it runs near-outermost and wraps every response
    # — the console's own JS/CSS included, not just the API's JSON. (Starlette
    # applies middleware in reverse registration order, the same rule
    # WebConsoleMiddleware above depends on.) Only SecurityHeadersMiddleware
    # below sits outside it, which is the right way round: those headers are
    # appended to whatever this produced, compressed or not.
    #
    # Not a micro-optimisation. `/devices/{id}/series` answers the history
    # screen with ~2 MB of JSON: 720 time buckets each repeating the same ~35
    # field names, across five domains. Measured on real data in that shape it
    # is ~11x compressible, so the phone was pulling ~1.9 MB where ~170 KB
    # would do. The console never showed it because it talks to this process
    # over loopback on the machine serving it; a phone on the Funnel pays for
    # every byte.
    #
    # Two exclusions beyond Starlette's defaults, both already-compressed
    # payloads where gzip burns CPU and saves nothing: the APK, and the
    # PyInstaller agent binaries (the only octet-stream this API serves).
    # Range requests need no exclusion — the middleware already declines to
    # touch a 206, which is what keeps a resumed download working.
    app.add_middleware(
        GZipMiddleware,
        minimum_size=1024,
        exclude_content_types=(
            *DEFAULT_EXCLUDED_CONTENT_TYPES,
            "application/octet-stream",
            "application/vnd.android.package-archive",
        ),
    )

    # Added last, so it runs FIRST: every response leaves through here, and no
    # route — not the API, not the console's static files, not an agent binary
    # download — can fail to carry these by forgetting to. See
    # app/security/headers.py for what each header is doing and, more usefully,
    # for what the console actually loads that the CSP had to be written
    # around.
    app.add_middleware(SecurityHeadersMiddleware)

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
