"""Public demo extraction endpoint — no API key required."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.core.language import detect_language
from app.core.llm import extract_with_llm
from app.core.prompts import PROMPT_VERSION, build_prompt
from app.core.rate_limit import limiter
from app.core.sanitize import sanitize, wrap_for_llm
from app.core.schemas import ExtractionMeta, ReviewExtraction, ReviewRequest

router = APIRouter(prefix="/demo", tags=["demo"])
log = structlog.get_logger(__name__)

_SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Process-local in-memory LRU cache for the keyless demo endpoint.
#
# Rationale: the demo endpoint has no org, so it cannot use the org-scoped
# ``extractions`` Postgres table.  Instead we keep a small bounded dict
# (max 256 entries, evict the LRU entry when full) that lives for the
# lifetime of the Cloud Run process.  Lost on cold-start; warm instances
# benefit from it — accepted free-tier design.
#
# Thread/async safety: CPython's GIL makes dict reads/writes and
# OrderedDict.move_to_end atomic at the bytecode level.  Under asyncio
# (single-threaded event loop) there is no concurrent mutation risk, so no
# additional lock is required.
# ---------------------------------------------------------------------------

_DEMO_CACHE_MAX_SIZE: int = 256
_demo_cache: OrderedDict[str, ReviewExtraction] = OrderedDict()


def _demo_cache_key(normalized_text: str) -> str:
    """Return the SHA-256 hex digest of the already-normalized review text."""
    return hashlib.sha256(normalized_text.encode()).hexdigest()


def _demo_cache_get(key: str) -> ReviewExtraction | None:
    """Retrieve an entry and move it to the MRU end (O(1))."""
    entry = _demo_cache.get(key)
    if entry is not None:
        _demo_cache.move_to_end(key)
    return entry


def _demo_cache_put(key: str, value: ReviewExtraction) -> None:
    """Insert an entry, evicting the LRU entry when the cache is full."""
    if key in _demo_cache:
        _demo_cache.move_to_end(key)
        _demo_cache[key] = value
        return
    if len(_demo_cache) >= _DEMO_CACHE_MAX_SIZE:
        _demo_cache.popitem(last=False)  # evict least-recently-used
    _demo_cache[key] = value


def demo_cache_clear() -> None:
    """Clear the demo cache.  Exposed for test isolation only."""
    _demo_cache.clear()


def demo_cache_size() -> int:
    """Return the current number of cached demo results.  For tests."""
    return len(_demo_cache)


@router.post("/extract", response_model=ReviewExtraction)
@limiter.limit("5/minute")
async def demo_extract(request: Request, body: ReviewRequest) -> ReviewExtraction:
    """Keyless demo extraction. Rate-limited. No results stored.

    Repeated identical reviews are served from a process-local in-memory LRU
    cache (max 256 entries) without re-spending LLM tokens.

    Use POST /v2/extract with a riq_live_* API key for production use.
    """
    clean_text, _ = sanitize(body.text)
    cache_key = _demo_cache_key(clean_text)

    cached = _demo_cache_get(cache_key)
    if cached is not None:
        log.info("demo.cache_hit", cache_key=cache_key[:16])
        return cached

    detected_lang = detect_language(clean_text)
    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_prompt(wrapped, detected_lang)

    try:
        llm_output, model_name, latency_ms, _, _, _ = await extract_with_llm(
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
    result = ReviewExtraction(
        **llm_output.model_dump(),
        review_length_chars=len(body.text),
        extraction_meta=meta,
    )
    _demo_cache_put(cache_key, result)
    log.info("demo.extract", model=model_name, lang=detected_lang, latency_ms=latency_ms)
    return result
