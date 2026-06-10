"""Tiered router — selects small vs large Groq model and handles escalation.

Called by extract_with_llm when enable_tiered_routing=True.
Returns the extraction result or raises RuntimeError on Groq exhaustion
(caller then falls back to secondary / Gemini / 503).
"""

from __future__ import annotations

import json

import structlog
from groq import APIError, APIStatusError
from pydantic import ValidationError

from app.core.config import Settings
from app.core.language import detect_language
from app.core.metrics import ROUTER_ESCALATIONS_TOTAL, ROUTER_TIER_TOKENS_IN, ROUTER_TIER_TOTAL
from app.core.providers.base import assert_privacy_safe
from app.core.providers.groq import GroqProvider
from app.core.routing_policy import choose_tier, escalation_triggers
from app.core.schemas import ReviewExtractionLLMOutput

log = structlog.get_logger(__name__)


def _parse_response(raw: str) -> ReviewExtractionLLMOutput:
    """Parse raw LLM text → validated Pydantic model (strips markdown fences)."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return ReviewExtractionLLMOutput.model_validate(json.loads(text))


async def _call_provider(
    provider: GroqProvider,
    user_prompt: str,
    system_prompt: str,
    *,
    max_retries: int,
) -> tuple[str, int, int]:
    """Call a GroqProvider with parse-error retries.

    Returns (raw_text, tokens_in, tokens_out).
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    api_error_count = 0
    for attempt in range(max_retries + 1):
        try:
            return await provider.complete(
                user_prompt,
                system_prompt=system_prompt,
                retry=(attempt > 0),
            )
        except (ValidationError, json.JSONDecodeError) as exc:
            last_exc = exc
            log.warning("router.parse_error", model=provider.model, attempt=attempt)
        except (APIError, APIStatusError) as exc:
            api_error_count += 1
            last_exc = exc
            log.warning("router.api_error", model=provider.model, attempt=api_error_count)
            if api_error_count >= 2:
                break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("router.unexpected_error", model=provider.model, error=str(exc))
            break
    raise RuntimeError(f"Groq provider exhausted: {last_exc}") from last_exc


async def route_extraction(
    user_prompt: str,
    system_prompt: str,
    *,
    allow_gemini_fallback: bool,
    settings: Settings,
) -> tuple[ReviewExtractionLLMOutput, str, int, int, bool]:
    """Route a single extraction through the tiered model selection policy.

    Args:
        user_prompt: The fully formatted prompt (includes the review text).
        system_prompt: The system instruction string.
        allow_gemini_fallback: Forwarded from extract_with_llm; False on org-key path.
        settings: Application settings (injected to avoid repeated lru_cache calls).

    Returns:
        Tuple of (extraction, model_name, tokens_in, tokens_out, escalated).
        ``escalated`` is True when the small model triggered escalation to large.

    Raises:
        RuntimeError: When Groq is fully exhausted (caller falls back to secondary/Gemini).
    """
    # Language detection — runs on the prompt which embeds the review text.
    lang = detect_language(user_prompt)
    initial_tier = choose_tier(lang)

    large_provider = GroqProvider(
        model=settings.groq_model_large,
        api_key=settings.groq_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    if not allow_gemini_fallback:
        assert_privacy_safe(large_provider)

    if initial_tier == "large":
        # hi-en: route directly to large model — eval shows it is the hard bucket.
        raw, tin, tout = await _call_provider(
            large_provider,
            user_prompt,
            system_prompt,
            max_retries=settings.llm_max_retries,
        )
        large_extraction = _parse_response(raw)
        ROUTER_TIER_TOTAL.labels(tier="large").inc()
        ROUTER_TIER_TOKENS_IN.labels(tier="large").inc(tin)
        log.info(
            "router.extracted",
            lang=lang,
            tier="large",
            escalated=False,
            model=settings.groq_model_large,
            tokens_in=tin,
            tokens_out=tout,
        )
        return large_extraction, settings.groq_model_large, tin, tout, False

    # en / hi: try small model first.
    small_provider = GroqProvider(
        model=settings.groq_model_small,
        api_key=settings.groq_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    if not allow_gemini_fallback:
        assert_privacy_safe(small_provider)

    small_tin = small_tout = 0
    extraction: ReviewExtractionLLMOutput | None = None
    schema_valid = False

    try:
        raw, small_tin, small_tout = await _call_provider(
            small_provider,
            user_prompt,
            system_prompt,
            max_retries=settings.llm_max_retries,
        )
        try:
            extraction = _parse_response(raw)
            schema_valid = True
        except (ValidationError, json.JSONDecodeError):
            schema_valid = False
    except RuntimeError:
        # Small model exhausted — escalate.
        log.warning("router.small_exhausted_escalating", lang=lang, model=settings.groq_model_small)

    triggers = escalation_triggers(extraction, schema_valid=schema_valid)
    if not triggers:
        # Small model result is good — return it.
        assert extraction is not None
        ROUTER_TIER_TOTAL.labels(tier="small").inc()
        ROUTER_TIER_TOKENS_IN.labels(tier="small").inc(small_tin)
        log.info(
            "router.extracted",
            lang=lang,
            tier="small",
            escalated=False,
            model=settings.groq_model_small,
            tokens_in=small_tin,
            tokens_out=small_tout,
        )
        return extraction, settings.groq_model_small, small_tin, small_tout, False

    # Escalate to large model.
    log.info(
        "router.escalating",
        lang=lang,
        triggers=triggers,
        small_model=settings.groq_model_small,
        large_model=settings.groq_model_large,
    )
    raw, large_tin, large_tout = await _call_provider(
        large_provider,
        user_prompt,
        system_prompt,
        max_retries=settings.llm_max_retries,
    )
    extraction = _parse_response(raw)
    total_tin = small_tin + large_tin
    total_tout = small_tout + large_tout
    ROUTER_TIER_TOTAL.labels(tier="large").inc()
    ROUTER_ESCALATIONS_TOTAL.inc()
    ROUTER_TIER_TOKENS_IN.labels(tier="small").inc(small_tin)
    ROUTER_TIER_TOKENS_IN.labels(tier="large").inc(large_tin)
    log.info(
        "router.extracted",
        lang=lang,
        tier="large",
        escalated=True,
        model=settings.groq_model_large,
        tokens_in=total_tin,
        tokens_out=total_tout,
    )
    return extraction, settings.groq_model_large, total_tin, total_tout, True
