"""Public demo extraction endpoint — no API key required."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import get_settings
from app.core.injection_guard import classify_injection_risk
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
# shares the SAME Groq API key -- and its SAME free-tier budget -- as every real paying
# customer's /v2/extract call (app/core/config.py has exactly one groq_api_key).
#
# CORRECTED Session 13 (see docs/architecture/adr/0015-panel-restoration-and-quota-safety-gap.md's
# Session 13 correction): the limits are PER MODEL (openai/gpt-oss-20b and
# openai/gpt-oss-120b each independently get 30 RPM / 1,000 RPD / 8,000 TPM / 200,000
# TPD), not one shared 200K-tokens/day org-wide pool as an earlier version of this
# comment claimed. At the real measured tokens/extraction per tier
# (eval/results/token_cost_measurement_n106.json), TPD binds long before RPD does --
# the real combined ceiling across BOTH models, shared by every consumer of this key
# (real customers, this endpoint, live eval calls), is ~140.6 extractions/day
# (eval/capacity_model.py). This is an AVAILABILITY risk, not a billing risk (free tier
# has no bill) -- and it is a far tighter ceiling than "1,000 requests/day" alone
# suggests.
#
# DEMO_DAILY_REQUEST_BUDGET is sized to leave real customers most of that real ~140.6/day
# ceiling: 50 demo requests/day is roughly a third of it, even before accounting for the
# in-process LRU cache absorbing repeated identical text at zero marginal cost.
_DEFAULT_DEMO_DAILY_BUDGET = 50


def _effective_demo_daily_budget() -> int:
    """Return the daily demo-request budget to enforce right now.

    Session 12 P7a: evaluated fresh on every call (not baked into a module-level
    constant at import time) so a TTL can actually expire within a running instance's
    lifetime. An override away from `_DEFAULT_DEMO_DAILY_BUDGET` is only honored while
    `DEMO_DAILY_REQUEST_BUDGET_OVERRIDE_EXPIRES_AT` names a future UTC timestamp --
    missing, unparseable, or past, and the override is ignored and the safe default is
    used instead. Fails toward the SAFE default (real demo traffic keeps working), not
    toward the overridden value, on any ambiguity -- the incident this exists to prevent
    was an override silently left in place returning 429s to real visitors, not one
    silently expiring a moment too early.
    """
    settings = get_settings()
    configured = settings.demo_daily_request_budget
    if configured == _DEFAULT_DEMO_DAILY_BUDGET:
        return configured

    expires_at_raw = settings.demo_daily_request_budget_override_expires_at
    if not expires_at_raw:
        log.error(
            "demo.budget_override_missing_ttl",
            configured=configured,
            default=_DEFAULT_DEMO_DAILY_BUDGET,
        )
        return _DEFAULT_DEMO_DAILY_BUDGET

    try:
        expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
    except ValueError:
        log.error("demo.budget_override_invalid_expiry", raw=expires_at_raw)
        return _DEFAULT_DEMO_DAILY_BUDGET

    if datetime.now(UTC) >= expires_at:
        log.warning(
            "demo.budget_override_expired",
            configured=configured,
            expired_at=expires_at_raw,
        )
        return _DEFAULT_DEMO_DAILY_BUDGET

    return configured


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
            check_and_increment_demo_request_pg, _effective_demo_daily_budget()
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
    clean_text, regex_suspicious = sanitize(body.text)
    cache_key = _demo_cache_key(clean_text)

    cached = _demo_cache_get(cache_key)
    if cached is not None:
        log.info("demo.cache_hit", cache_key=cache_key[:16])
        return cached

    # Global daily quota gate -- BEFORE spending any tokens. See DEMO_DAILY_REQUEST_
    # BUDGET's docstring above for why this exists and how the number was chosen.
    if not await _check_demo_quota():
        log.warning("demo.quota_exhausted", daily_budget=_effective_demo_daily_budget())
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

    # Session 13 P4a: same second layer as /v2/extract -- see
    # app/core/injection_guard.py's module docstring. The public, keyless demo is arguably
    # the higher-value target for this check (no API key needed to reach it at all).
    guard_suspicious = await classify_injection_risk(body.text, api_key=get_settings().groq_api_key)
    if regex_suspicious or guard_suspicious:
        log.warning(
            "demo.suspicious_input",
            regex_flagged=regex_suspicious,
            guard_flagged=guard_suspicious,
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
