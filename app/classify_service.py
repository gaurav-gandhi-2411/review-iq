"""The PRIVATE classify service: only POST /v2/classify, no API key, no database (S27).

Deployed as its own Cloud Run service with `--no-allow-unauthenticated`: Cloud Run verifies the caller's Google identity token against
IAM (`roles/run.invoker`, granted to the review-iq service account only) before a request reaches this code, so there is no
application-level auth and no DB credential here. Never expose it with allUsers / allAuthenticatedUsers; the deploy script asserts
the IAM policy and that an unauthenticated request is refused. The public route in app/api/v2/classify.py (API key, flag
ENABLE_V2_CLASSIFY, default off) is unchanged and shares `run_classify`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v2.classify import ClassifyRequest, ClassifyResponse, run_classify
from app.core.config import get_settings

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Load the model before the first request so a cold start pays for it once, at startup. A failure is logged, not fatal:
    requests then answer the typed 503 (`classify_artifacts_missing`) instead of the container crash-looping."""
    try:
        from app.core.classify import runtime

        s = get_settings()
        await asyncio.to_thread(
            runtime.get_backend, s.classify_artifact_dir, s.classify_torch_threads
        )
        log.info("classify_service.model_loaded")
    except Exception as e:  # noqa: BLE001 (startup must not crash-loop; the request path reports the typed error)
        log.warning("classify_service.model_load_failed", error=type(e).__name__)
    yield


app = FastAPI(
    title="review-iq classify (private)",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v2/classify", response_model=ClassifyResponse)
async def classify_private(body: ClassifyRequest) -> ClassifyResponse | JSONResponse:
    return await run_classify(body, "internal")
