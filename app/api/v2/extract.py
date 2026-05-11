"""POST /v2/extract and POST /v2/extract/batch endpoints."""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.llm import extract_with_llm
from app.core.metrics import EXTRACTION_LATENCY, EXTRACTIONS_TOTAL
from app.core.prompt import PROMPT_VERSION, build_user_prompt
from app.core.sanitize import sanitize, wrap_for_llm
from app.core.schemas import (
    BatchReviewRequest,
    ExtractionMetaV2,
    ReviewExtractionV2,
    ReviewRequest,
)
from app.core.storage_pg import get_by_hash_pg, save_extraction_pg, update_usage_tokens

router = APIRouter(prefix="/v2", tags=["v2"])
log = structlog.get_logger(__name__)

_SCHEMA_VERSION = "1.0.0"


async def _run_extraction_v2(request: ReviewRequest, ctx: ApiKeyContext) -> ReviewExtractionV2:
    """Core extraction pipeline for a single review (v2, Postgres-backed)."""
    input_hash = request.input_hash()

    import asyncio

    cached = await asyncio.to_thread(get_by_hash_pg, ctx.org_id, input_hash)
    if cached is not None:
        log.info("extraction.cache_hit", input_hash=input_hash, org_id=ctx.org_id)
        EXTRACTIONS_TOTAL.labels(model="cached", cached="true").inc()
        return cached

    clean_text, is_suspicious = sanitize(request.text)
    if is_suspicious:
        log.warning("extraction.suspicious_input", input_hash=input_hash)

    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_user_prompt(wrapped)

    t0 = datetime.utcnow()
    llm_output, model_name, latency_ms, tokens_in, tokens_out = await extract_with_llm(user_prompt)

    meta = ExtractionMetaV2(
        model=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=_SCHEMA_VERSION,
        extracted_at=t0,
        latency_ms=latency_ms,
        input_hash=input_hash,
        org_id=ctx.org_id,
    )
    extraction = ReviewExtractionV2(
        **llm_output.model_dump(),
        review_length_chars=len(request.text),
        extraction_meta=meta,
    )

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
    )
    # Update token counts on the usage_record created during auth.
    # On LLM failure this is never reached — the record stays at 0/0
    # (quota slot consumed, no tokens charged — see ARCHITECTURE.md).
    await asyncio.to_thread(
        update_usage_tokens,
        ctx.usage_record_id, tokens_in, tokens_out,
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
    return extraction


@router.post("/extract", response_model=ReviewExtractionV2)
async def extract_single(
    body: ReviewRequest,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> ReviewExtractionV2:
    """Extract structured insights from a single review (v2, multi-tenant)."""
    try:
        return await _run_extraction_v2(body, ctx)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


async def _process_batch_v2(ctx: ApiKeyContext, reviews: list[ReviewRequest]) -> None:
    """Background task: process batch reviews (v2). Fire-and-forget — no job tracking."""
    processed = failed = 0
    for req in reviews:
        try:
            await _run_extraction_v2(req, ctx)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            log.error("batch.item_failed", org_id=ctx.org_id, error=str(exc))
            failed += 1
    log.info("batch.completed", org_id=ctx.org_id, processed=processed, failed=failed)


@router.post("/extract/batch", status_code=status.HTTP_202_ACCEPTED)
async def extract_batch(
    body: BatchReviewRequest,
    background_tasks: BackgroundTasks,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> dict[str, str]:
    """Submit a batch of reviews for async extraction (v2).

    Returns immediately with a count. No job-tracking in Phase 2 —
    results are queryable via GET /v2/reviews once processing completes.
    """
    background_tasks.add_task(_process_batch_v2, ctx, body.reviews)
    log.info("batch.submitted", org_id=ctx.org_id, total=len(body.reviews))
    return {"status": "accepted", "total": str(len(body.reviews))}
