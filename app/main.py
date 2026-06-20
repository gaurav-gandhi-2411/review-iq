"""Review IQ — FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.account import router as account_router
from app.api.admin import router as admin_router
from app.api.bff.router import router as bff_router
from app.api.dashboard import router as dashboard_router
from app.api.demo import router as demo_router
from app.api.extract import router as extract_router
from app.api.ops import router as ops_router
from app.api.query import router as query_router
from app.api.v2.authenticity import router as v2_authenticity_router
from app.api.v2.corrections import router as v2_corrections_router
from app.api.v2.dataset import router as v2_dataset_router
from app.api.v2.extract import router as v2_extract_router
from app.api.v2.ingest import router as ingest_router
from app.api.v2.insights import router as v2_insights_router
from app.api.v2.reply import router as v2_reply_router
from app.api.v2.reviews import router as v2_reviews_router
from app.auth.signup import router as signup_router
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.core.metrics import PrometheusMiddleware
from app.core.rate_limit import limiter
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

    _app = FastAPI(
        title="Review IQ",
        description="Unstructured customer reviews → queryable structured insights.",
        version="0.2.0",
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "v2",
                "description": "Multi-tenant endpoints (Postgres-backed). Requires riq_live_* API key.",
            },
            {"name": "extraction", "description": "v1 single-tenant extraction (SQLite-backed)."},
            {"name": "query", "description": "v1 query and analytics (SQLite-backed)."},
            {
                "name": "admin",
                "description": "Admin endpoints — org and key management. Requires HTTP Basic auth.",
            },
            {"name": "ops", "description": "Health check and Prometheus metrics."},
        ],
    )

    _app.state.limiter = limiter
    _app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Middleware order (last add_middleware = outermost = first to process requests):
    #   SlowAPIMiddleware → PrometheusMiddleware → CORSMiddleware → route handler
    # CORS is innermost so it handles preflight OPTIONS before rate-limit counters advance.
    # CORS — explicit allowlist only. Wildcard must never reach production.
    # Origins configured via ALLOWED_ORIGINS env var (comma-separated).
    # Default covers local dev + demo Pages site; production Cloud Run sets
    # ALLOWED_ORIGINS to the locked web-app origin before the web app deploys.
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    _app.add_middleware(PrometheusMiddleware)
    _app.add_middleware(SlowAPIMiddleware)

    # Ops (health + metrics) — always mounted, unauthenticated
    _app.include_router(ops_router)

    # v2, admin, and demo are always mounted
    _app.include_router(v2_extract_router)
    _app.include_router(ingest_router)
    _app.include_router(v2_reviews_router)
    _app.include_router(v2_authenticity_router)
    _app.include_router(v2_insights_router)
    _app.include_router(v2_reply_router)
    _app.include_router(v2_corrections_router)
    _app.include_router(v2_dataset_router)
    _app.include_router(bff_router)
    _app.include_router(admin_router)
    _app.include_router(signup_router)
    _app.include_router(account_router)
    _app.include_router(demo_router)

    if settings.deploy_target != "cloud-run":
        _app.include_router(dashboard_router)
        _app.include_router(extract_router)
        _app.include_router(query_router)

    @_app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus metrics in text exposition format."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return _app


app = create_app()
