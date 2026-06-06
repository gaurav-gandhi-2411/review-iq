"""Public demo extraction endpoint — no API key required."""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, status

from app.core.language import detect_language
from app.core.llm import extract_with_llm
from app.core.prompts import PROMPT_VERSION, build_prompt
from app.core.sanitize import sanitize, wrap_for_llm
from app.core.schemas import ExtractionMeta, ReviewExtraction, ReviewRequest

router = APIRouter(prefix="/demo", tags=["demo"])
log = structlog.get_logger(__name__)

_SCHEMA_VERSION = "1.0.0"


@router.post("/extract", response_model=ReviewExtraction)
async def demo_extract(body: ReviewRequest) -> ReviewExtraction:
    """Keyless demo extraction. Rate-limited. No results stored.

    Use POST /v2/extract with a riq_live_* API key for production use.
    """
    clean_text, _ = sanitize(body.text)
    detected_lang = detect_language(clean_text)
    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_prompt(wrapped, detected_lang)

    try:
        llm_output, model_name, latency_ms, _, _ = await extract_with_llm(
            user_prompt, allow_gemini_fallback=False
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upstream LLM unavailable — try again in a moment.",
            headers={"Retry-After": "30"},
        ) from exc

    meta = ExtractionMeta(
        model=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=_SCHEMA_VERSION,
        extracted_at=datetime.utcnow(),
        latency_ms=latency_ms,
        input_hash=body.input_hash(),
    )
    log.info("demo.extract", model=model_name, lang=detected_lang, latency_ms=latency_ms)
    return ReviewExtraction(
        **llm_output.model_dump(),
        review_length_chars=len(body.text),
        extraction_meta=meta,
    )
