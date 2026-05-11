"""POST /extract and POST /extract/batch endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.auth import require_api_key
from app.core.llm import extract_with_llm
from app.core.metrics import EXTRACTION_LATENCY, EXTRACTIONS_TOTAL
from app.core.prompt import PROMPT_VERSION, build_user_prompt
from app.core.sanitize import sanitize, wrap_for_llm
from app.core.schemas import (
    BatchJob,
    BatchReviewRequest,
    ExtractionMeta,
    JobStatus,
    ReviewExtraction,
    ReviewRequest,
)
from app.core.storage import (
    create_batch_job,
    get_batch_job,
    get_by_hash,
    save_extraction,
    update_batch_job,
)

router = APIRouter(prefix="/extract", tags=["extraction"])
log = structlog.get_logger(__name__)


async def _run_extraction(request: ReviewRequest) -> ReviewExtraction:
    """Core extraction pipeline for a single review."""
    input_hash = request.input_hash()

    cached = await get_by_hash(input_hash)
    if cached is not None:
        log.info("extraction.cache_hit", input_hash=input_hash)
        EXTRACTIONS_TOTAL.labels(model="cached", cached="true").inc()
        return cached

    clean_text, is_suspicious = sanitize(request.text)
    if is_suspicious:
        log.warning("extraction.suspicious_input", input_hash=input_hash)

    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_user_prompt(wrapped)

    t0 = datetime.utcnow()
    llm_output, model_name, latency_ms, _, _ = await extract_with_llm(user_prompt)

    meta = ExtractionMeta(
        model=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version="1.0.0",
        extracted_at=t0,
        latency_ms=latency_ms,
        input_hash=input_hash,
    )
    extraction = ReviewExtraction(
        **llm_output.model_dump(),
        review_length_chars=len(request.text),
        extraction_meta=meta,
    )

    await save_extraction(input_hash, request.text, extraction)
    EXTRACTIONS_TOTAL.labels(model=model_name, cached="false").inc()
    EXTRACTION_LATENCY.labels(model=model_name).observe(latency_ms)
    log.info(
        "extraction.completed",
        product=extraction.product,
        model=model_name,
        latency_ms=latency_ms,
    )
    return extraction


@router.post("", response_model=ReviewExtraction)
async def extract_single(
    body: ReviewRequest,
    _key: str = Depends(require_api_key),
) -> ReviewExtraction:
    """Extract structured insights from a single customer review.

    Returns cached result if the same review was already processed.
    """
    try:
        return await _run_extraction(body)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


async def _process_batch(job_id: str, reviews: list[ReviewRequest]) -> None:
    """Background task: process a batch job review-by-review."""
    processed = 0
    failed = 0
    await update_batch_job(job_id, status=JobStatus.processing)

    for req in reviews:
        try:
            await _run_extraction(req)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            log.error("batch.item_failed", job_id=job_id, error=str(exc))
            failed += 1
        await update_batch_job(job_id, processed=processed, failed=failed)

    final_status = JobStatus.done if failed == 0 else JobStatus.failed
    await update_batch_job(job_id, status=final_status)
    log.info("batch.completed", job_id=job_id, processed=processed, failed=failed)


@router.post("/batch", response_model=BatchJob, status_code=status.HTTP_202_ACCEPTED)
async def extract_batch(
    body: BatchReviewRequest,
    background_tasks: BackgroundTasks,
    _key: str = Depends(require_api_key),
) -> BatchJob:
    """Submit a batch of reviews for async extraction.

    Returns a job ID immediately. Poll GET /extract/batch/{job_id} for status.
    """
    job_id = str(uuid.uuid4())
    total = len(body.reviews)
    await create_batch_job(job_id, total)
    background_tasks.add_task(_process_batch, job_id, body.reviews)
    log.info("batch.created", job_id=job_id, total=total)
    return BatchJob(job_id=job_id, total=total)


@router.get("/batch/{job_id}", response_model=BatchJob)
async def get_batch_status(job_id: str) -> BatchJob:
    """Poll the status of a batch extraction job."""
    job = await get_batch_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch job '{job_id}' not found.",
        )
    return job
