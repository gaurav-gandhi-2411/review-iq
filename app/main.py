"""Review IQ — FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.admin import router as admin_router
from app.api.dashboard import router as dashboard_router
from app.api.extract import router as extract_router
from app.api.query import router as query_router
from app.api.v2.extract import router as v2_extract_router
from app.api.v2.reviews import router as v2_reviews_router
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.metrics import PrometheusMiddleware
from app.core.storage import migrate

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    log.info("app.startup")
    if get_settings().deploy_target != "cloud-run":
        await migrate()
    yield
    log.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    )

    _app = FastAPI(
        title="Review IQ",
        description="Unstructured customer reviews → queryable structured insights.",
        version="0.2.0",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "v2", "description": "Multi-tenant endpoints (Postgres-backed). Requires riq_live_* API key."},
            {"name": "extraction", "description": "v1 single-tenant extraction (SQLite-backed)."},
            {"name": "query", "description": "v1 query and analytics (SQLite-backed)."},
            {"name": "admin", "description": "Admin endpoints — org and key management. Requires HTTP Basic auth."},
            {"name": "ops", "description": "Health check and Prometheus metrics."},
        ],
    )

    _app.state.limiter = limiter
    _app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    _app.add_middleware(PrometheusMiddleware)

    # v2 and admin are always mounted
    _app.include_router(v2_extract_router)
    _app.include_router(v2_reviews_router)
    _app.include_router(admin_router)

    if settings.deploy_target != "cloud-run":
        _app.include_router(dashboard_router)
        _app.include_router(extract_router)
        _app.include_router(query_router)

    @_app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Health check — returns 200 when the service is running."""
        return {"status": "ok"}

    @_app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus metrics in text exposition format."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return _app


app = create_app()
