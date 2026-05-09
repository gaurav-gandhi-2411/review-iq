"""Review IQ — FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.extract import router as extract_router
from app.api.query import router as query_router
from app.core.config import get_settings
from app.core.storage import migrate

log = structlog.get_logger(__name__)


def _get_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(
        key_func=get_remote_address,
        default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    )


limiter = _get_limiter()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("app.startup")
    await migrate()
    yield
    log.info("app.shutdown")


app = FastAPI(
    title="Review IQ",
    description="Unstructured customer reviews → queryable structured insights.",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(extract_router)
app.include_router(query_router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Health check — returns 200 when the service is running."""
    return {"status": "ok"}


@app.get("/metrics", tags=["ops"])
async def metrics() -> JSONResponse:
    """Prometheus-compatible metrics (placeholder; expanded in observability step)."""
    return JSONResponse(content={"status": "metrics_coming_soon"})
