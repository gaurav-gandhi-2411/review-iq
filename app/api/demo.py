"""Public demo extraction endpoint — no API key required."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.language import detect_language
from app.core.llm import extract_with_llm
from app.core.pricing import UnknownModelError, price_extraction
from app.core.prompts import PROMPT_VERSION, build_prompt
from app.core.rate_limit import limiter
from app.core.sanitize import sanitize, wrap_for_llm
from app.core.schemas import ExtractionMeta, ReviewExtraction, ReviewRequest
from app.core.storage_pg import (
    check_and_increment_demo_request_pg,
    record_demo_extraction_cost_pg,
)

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


# ---------------------------------------------------------------------------
# Global (cross-IP) daily demo quota.
#
# The per-IP slowapi limit (5/minute) has no cross-IP cap at all, and this endpoint
# shares the SAME Groq API key -- and its SAME free-tier daily token/request budget --
# as every real paying customer's /v2/extract call (app/core/config.py has exactly one
# groq_api_key). Groq's free tier for the models this app actually runs
# (openai/gpt-oss-20b, openai/gpt-oss-120b) is 200,000 tokens/day and 1,000
# requests/day, shared across every call this key makes. Measured average tokens per
# real extraction (this repo's own eval run, grouped by language): en ~1833, hi ~1019,
# hi-en ~1934. At those rates, as few as ~103 (hi-en, worst case) to ~196 (hi, best
# case) unauthenticated demo calls in one day could exhaust the ENTIRE shared budget --
# after which real customers' extraction calls degrade or fail for the rest of that
# day. This is an AVAILABILITY risk, not a billing risk (free tier has no bill).
#
# DEMO_DAILY_REQUEST_BUDGET is sized to leave real customers most of the shared budget
# even in the worst case: 50 requests/day * 1934 tokens (hi-en, the most expensive
# language) = 96,700 tokens -- under half of the 200,000 daily ceiling, even if every
# single demo call happened to be the most expensive kind and zero were cache hits.
DEMO_DAILY_REQUEST_BUDGET = get_settings().demo_daily_request_budget


async def _check_demo_quota() -> bool:
    """Return True if today's global demo budget has room for one more real call.

    Fails CLOSED, not open: if the quota-check DB call itself errors (e.g. transient
    connection issue), this returns False -- treating "couldn't verify" as "budget
    exhausted" rather than silently letting unlimited demo traffic through, since the
    entire point of this check is protecting a resource real paying customers depend
    on. A demo-endpoint 429 is a much smaller cost than a real customer's extraction
    failing because the shared Groq quota was burned by unauthenticated demo traffic.
    """
    try:
        return await asyncio.to_thread(
            check_and_increment_demo_request_pg, DEMO_DAILY_REQUEST_BUDGET
        )
    except Exception:
        log.error("demo.quota_check_failed", exc_info=True)
        return False


@router.post(
    "/extract",
    response_model=ReviewExtraction,
    summary="Keyless demo extraction (5/minute, no auth)",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "text": "Great sound quality but the battery dies after 3 hours. "
                        "Would still recommend for the price.",
                    },
                },
            },
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "product": "wireless headphones",
                            "stars_inferred": 4,
                            "pros": ["great sound quality"],
                            "cons": ["battery dies after 3 hours"],
                            "sentiment": "mixed",
                            "topics": ["sound quality", "battery life"],
                            "urgency": "low",
                            "language": "en",
                        },
                    },
                },
            },
        },
    },
)
@limiter.limit("5/minute")
async def demo_extract(request: Request, body: ReviewRequest) -> ReviewExtraction:
    """Keyless demo extraction. Rate-limited to 5/minute per IP. No results stored.

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

    # Global daily quota gate -- BEFORE spending any tokens. See DEMO_DAILY_REQUEST_
    # BUDGET's docstring above for why this exists and how the number was chosen.
    if not await _check_demo_quota():
        log.warning("demo.quota_exhausted", daily_budget=DEMO_DAILY_REQUEST_BUDGET)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "The free public demo has reached its shared daily capacity and will "
                "reset at midnight UTC. This limit protects the same LLM quota real "
                "customers' API keys use. Sign up for an API key for guaranteed "
                "capacity: POST /v2/extract."
            ),
            headers={"Retry-After": "3600"},
        )

    detected_lang = detect_language(clean_text)
    wrapped = wrap_for_llm(clean_text)
    user_prompt = build_prompt(wrapped, detected_lang)

    try:
        llm_output, model_name, latency_ms, tokens_in, tokens_out, _ = await extract_with_llm(
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

    # Cost telemetry: a missing pricing entry must not fail a response that already
    # succeeded -- log loudly (pricing.py already logs at ERROR before raising) and
    # skip the cost row, same tolerance as app/api/v2/extract.py's org-path recording.
    try:
        cost = price_extraction(model_name, tokens_in, tokens_out)
        await asyncio.to_thread(
            record_demo_extraction_cost_pg,
            cost.provider,
            cost.model,
            cost.tier,
            detected_lang,
            tokens_in,
            tokens_out,
            cost.cost_usd,
            cost.cost_inr,
        )
    except UnknownModelError as exc:
        log.error("demo.cost_pricing_missing", model=model_name, error=str(exc))
    except Exception:
        # Cost recording is observability, not correctness -- never fail an already-
        # successful demo response because the cost INSERT hit a transient DB issue.
        log.error("demo.cost_recording_failed", exc_info=True)

    log.info("demo.extract", model=model_name, lang=detected_lang, latency_ms=latency_ms)
    return result
