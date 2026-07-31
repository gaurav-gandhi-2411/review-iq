"""POST /v2/extract and POST /v2/extract/batch endpoints."""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.alerts.engine import alert_on_review_event
from app.core.config import get_settings
from app.core.ingest_worker import drain_rows
from app.core.language import detect_language
from app.core.llm import extract_with_llm
from app.core.metrics import EXTRACTION_LATENCY, EXTRACTIONS_TOTAL
from app.core.prompts import PROMPT_VERSION, build_prompt
from app.core.sanitize import rehydrate_output, sanitize, wrap_for_llm
from app.core.schemas import (
    BatchReviewRequest,
    ExtractionMetaV2,
    ReviewExtractionV2,
    ReviewRequest,
)
from app.core.storage_pg import (
    count_pending_rows_pg,
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    get_by_hash_pg,
    save_extraction_pg,
    update_batch_job_pg,
    update_usage_tokens,
)

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)

_SCHEMA_VERSION = "1.0.0"


async def _run_extraction_v2(
    request: ReviewRequest, ctx: ApiKeyContext, product_override: str | None = None
) -> ReviewExtractionV2:
    """Core extraction pipeline for a single review (v2, Postgres-backed).

    `product_override`: when the ingestion source itself supplied a product value (CSV
    product_column, a connector's native product field), passed through from the caller (e.g.
    ingest_worker) and stored in preference to the LLM-inferred product -- see
    save_extraction_pg's docstring.
    """
    input_hash = request.input_hash()

    import asyncio

    cached = await asyncio.to_thread(get_by_hash_pg, ctx.org_id, input_hash)
    if cached is not None:
        log.info("extraction.cache_hit", input_hash=input_hash, org_id=ctx.org_id)
        EXTRACTIONS_TOTAL.labels(model="cached", cached="true").inc()
        # Re-evaluate on cache hit: this exact review text may have been extracted before
        # this alert wiring existed, so it may never have been checked for alert-worthiness.
        # Cheap to re-check — alert_log dedupe short-circuits if it really was already alerted.
        await alert_on_review_event(org_id=ctx.org_id, review_id=input_hash, extraction=cached)
        return cached

    detected_lang = detect_language(request.text)
    clean_text, is_suspicious, redaction_map = sanitize(request.text)
    if is_suspicious:
        log.warning("extraction.suspicious_input", input_hash=input_hash)

    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_prompt(wrapped, detected_lang)

    t0 = datetime.utcnow()
    llm_output, model_name, latency_ms, tokens_in, tokens_out, degraded = await extract_with_llm(
        user_prompt, allow_gemini_fallback=False
    )
    # Detected language takes precedence over LLM's self-reported language.
    llm_output.language = detected_lang

    meta = ExtractionMetaV2(
        model=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=_SCHEMA_VERSION,
        extracted_at=t0,
        latency_ms=latency_ms,
        input_hash=input_hash,
        org_id=ctx.org_id,
        degraded=degraded,
    )
    extraction = ReviewExtractionV2(
        **llm_output.model_dump(),
        review_length_chars=len(request.text),
        review_date=request.review_date,
        extraction_meta=meta,
    )
    # Rehydrate before persisting: the LLM never saw the real PII, but a placeholder
    # token it echoed back into cons/pros/etc. is restorable here from the map built
    # above -- see app.core.sanitize module docstring.
    extraction = rehydrate_output(extraction, redaction_map)

    await asyncio.to_thread(
        save_extraction_pg,
        ctx.org_id,
        ctx.api_key_id,
        input_hash,
        request.text,
        extraction,
        model_name,
        PROMPT_VERSION,
        _SCHEMA_VERSION,
        latency_ms,
        is_suspicious,
        request.review_date,
        product_override,
    )
    # Update token counts on the usage_record created during auth.
    # On LLM failure this is never reached — the record stays at 0/0
    # (quota slot consumed, no tokens charged — see ARCHITECTURE.md).
    # usage_record_id is "" for system/webhook-triggered extractions — skip accounting.
    if ctx.usage_record_id:
        await asyncio.to_thread(
            update_usage_tokens,
            ctx.org_id,
            ctx.usage_record_id,
            tokens_in,
            tokens_out,
        )
    EXTRACTIONS_TOTAL.labels(model=model_name, cached="false").inc()
    EXTRACTION_LATENCY.labels(model=model_name).observe(latency_ms)
    log.info(
        "extraction.completed",
        product=extraction.product,
        model=model_name,
        latency_ms=latency_ms,
        org_id=ctx.org_id,
    )
    await alert_on_review_event(org_id=ctx.org_id, review_id=input_hash, extraction=extraction)
    return extraction


_EXAMPLE_REVIEW_TEXT = (
    "Great sound quality but the battery dies after 3 hours. Would still recommend for the price."
)

_EXAMPLE_EXTRACTION_RESPONSE = {
    "product": "wireless headphones",
    "stars": None,
    "stars_inferred": 4,
    "pros": ["great sound quality"],
    "cons": ["battery dies after 3 hours"],
    "buy_again": True,
    "sentiment": "mixed",
    "topics": ["sound quality", "battery life"],
    "competitor_mentions": [],
    "urgency": "low",
    "feature_requests": [],
    "language": "en",
    "review_length_chars": 96,
    "confidence": 0.91,
    "extraction_meta": {
        "model": "llama-3.1-8b-instant",
        "prompt_version": "2.3",
        "schema_version": "1.0.0",
        "extracted_at": "2026-07-07T12:00:00Z",
        "latency_ms": 480,
        "input_hash": "sha256:9f2c...",
        "org_id": "5b6c1e2a-....",
        "degraded": False,
    },
}


@router.post(
    "/extract",
    response_model=ReviewExtractionV2,
    summary="Extract structured insights from one review",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"example": {"text": _EXAMPLE_REVIEW_TEXT}},
            },
        },
        "responses": {
            "200": {
                "content": {"application/json": {"example": _EXAMPLE_EXTRACTION_RESPONSE}},
            },
        },
    },
)
async def extract_single(
    body: ReviewRequest,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> ReviewExtractionV2:
    """Extract structured insights from a single review (v2, multi-tenant).

    Identical review text (same org) is served from cache — no LLM call, no
    quota spent, but still re-checked for alert-worthiness.
    """
    try:
        return await _run_extraction_v2(body, ctx)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="upstream LLM unavailable",
            headers={"Retry-After": "30"},
        ) from exc


async def _drain_until_batch_complete(org_id: str, job_id: str) -> None:
    """Background task: repeatedly drain rows until this batch job has no pending rows.

    Durable path (Option B of the CSV-throttling fix, 2026-07-09) — mirrors
    app.api.v2.ingest._drain_until_job_complete; see that function's docstring
    for why draining is global-queue-fair rather than job-scoped, and
    app/core/ingest_worker.py for the full design.
    """
    import asyncio

    settings = get_settings()
    while await asyncio.to_thread(count_pending_rows_pg, org_id, job_id) > 0:
        await drain_rows(settings.ingest_tick_rows)


@router.post(
    "/extract/batch",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit up to 100 reviews for async extraction",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {"reviews": [{"text": _EXAMPLE_REVIEW_TEXT}, {"text": "Terrible."}]},
                },
            },
        },
        "responses": {
            "202": {
                "content": {
                    "application/json": {
                        "example": {"status": "accepted", "total": "2", "job_id": "b1e2c3d4-...."}
                    }
                },
            },
        },
    },
)
async def extract_batch(
    body: BatchReviewRequest,
    background_tasks: BackgroundTasks,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> dict[str, str]:
    """Submit a batch of reviews (max 100) for async extraction (v2).

    Returns immediately with a job_id. Durable path (Option B, 2026-07-09):
    rows persist in batch_job_rows before processing starts, so a Cloud Run
    restart mid-batch is resumed by POST /internal/ingest/tick rather than
    silently dropping the remainder. Poll GET /v2/ingest/{job_id} for status,
    or query GET /v2/reviews once processing completes.
    """
    import asyncio
    import uuid

    job_id = str(uuid.uuid4())
    total = len(body.reviews)

    await asyncio.to_thread(create_batch_job_pg, ctx.org_id, job_id, total)
    await asyncio.to_thread(
        enqueue_batch_job_rows_pg,
        ctx.org_id,
        job_id,
        [r.text for r in body.reviews],
        None,
        [r.review_date for r in body.reviews],
    )
    await asyncio.to_thread(update_batch_job_pg, ctx.org_id, job_id, status="processing")

    background_tasks.add_task(_drain_until_batch_complete, ctx.org_id, job_id)

    log.info("batch.submitted", org_id=ctx.org_id, total=total, job_id=job_id)
    return {"status": "accepted", "total": str(total), "job_id": job_id}
