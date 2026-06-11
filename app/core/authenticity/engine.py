"""Authenticity scoring engine — wires LLM signal, heuristics, and batch signals."""

from __future__ import annotations

import json
from datetime import datetime

import structlog
from pydantic import BaseModel, ValidationError, field_validator

from app.core.authenticity.batch_signals import score_batch as _score_batch_signals
from app.core.authenticity.heuristics import compute_heuristic_score
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityResult
from app.core.config import Settings
from app.core.language import detect_language
from app.core.metrics import (
    AUTHENTICITY_DUPLICATE_CLUSTER_TOTAL,
    AUTHENTICITY_FLAG_TOTAL,
    AUTHENTICITY_LABEL_TOTAL,
)
from app.core.prompts.authenticity import build_authenticity_prompt
from app.core.providers.base import assert_privacy_safe
from app.core.providers.groq import GroqProvider

log = structlog.get_logger(__name__)

# Map LLM string values → AuthenticityFlag enum (LLM can only produce a subset)
_LLM_FLAG_MAP: dict[str, AuthenticityFlag] = {
    "incentivized_phrase": AuthenticityFlag.INCENTIVIZED_PHRASE,
    "rating_text_mismatch": AuthenticityFlag.RATING_TEXT_MISMATCH,
    "generic_low_info": AuthenticityFlag.GENERIC_LOW_INFO,
    "promotional_tone": AuthenticityFlag.PROMOTIONAL_TONE,
}


class _LLMAuthenticityOutput(BaseModel):
    score: float = 0.5
    flags: list[str] = []
    reasoning: str = ""

    @field_validator("score")
    @classmethod
    def clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


async def _call_authenticity_llm(
    review_text: str,
    language: str,
    settings: Settings,
) -> tuple[_LLMAuthenticityOutput, str, int, int]:
    """Call Groq with the authenticity prompt.

    Returns (output, model_name, tokens_in, tokens_out).
    Always uses groq_model_large (precision over recall).
    Calls assert_privacy_safe before completing.
    On any exception, logs a warning and returns a default neutral output
    (score=0.5, no flags).
    """
    provider = GroqProvider(
        model=settings.groq_model_large,
        api_key=settings.groq_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    neutral = _LLMAuthenticityOutput()
    try:
        assert_privacy_safe(provider, context="authenticity scoring")
        system_prompt, user_prompt = build_authenticity_prompt(review_text, language)
        raw, tokens_in, tokens_out = await provider.complete(
            user_prompt,
            system_prompt=system_prompt,
        )
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        output = _LLMAuthenticityOutput.model_validate(json.loads(text))
        return output, settings.groq_model_large, tokens_in, tokens_out
    except (json.JSONDecodeError, ValidationError) as exc:
        log.warning(
            "authenticity_engine.parse_error",
            model=settings.groq_model_large,
            error=str(exc),
        )
        return neutral, settings.groq_model_large, 0, 0
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "authenticity_engine.llm_error",
            model=settings.groq_model_large,
            error=str(exc),
        )
        return neutral, settings.groq_model_large, 0, 0


async def score_single(
    review_text: str,
    *,
    stars: int | None = None,
    settings: Settings,
) -> AuthenticityResult:
    """Score a single review. Combines LLM + heuristic signals.

    Calls _call_authenticity_llm then compute_heuristic_score.
    Converts any LLM flags that match AuthenticityFlag enum values.
    Combines via AuthenticityResult.from_signals.
    Records AUTHENTICITY_LABEL_TOTAL and AUTHENTICITY_FLAG_TOTAL metrics.
    """
    language = detect_language(review_text)

    llm_output, model_name, _tokens_in, _tokens_out = await _call_authenticity_llm(
        review_text, language, settings
    )

    heuristic_score, heuristic_flags = compute_heuristic_score(review_text, stars)

    # Convert LLM string flags to enum values (ignore unrecognised values)
    llm_flags: list[AuthenticityFlag] = [
        _LLM_FLAG_MAP[f] for f in llm_output.flags if f in _LLM_FLAG_MAP
    ]

    # Merge flags: deduplicate while preserving order (LLM first, then heuristic)
    seen: set[AuthenticityFlag] = set()
    merged_flags: list[AuthenticityFlag] = []
    for flag in llm_flags + heuristic_flags:
        if flag not in seen:
            seen.add(flag)
            merged_flags.append(flag)

    result = AuthenticityResult.from_signals(
        heuristic_score=heuristic_score,
        llm_score=llm_output.score,
        flags=merged_flags,
        reasons=llm_output.reasoning,
        review_text=review_text,
        model_used=model_name,
    )

    # Prometheus metrics
    AUTHENTICITY_LABEL_TOTAL.labels(label=result.label.value).inc()
    for flag in result.flags:
        AUTHENTICITY_FLAG_TOTAL.labels(flag=flag.value).inc()

    return result


async def score_batch(
    reviews: list[tuple[str, int | None]],
    *,
    dates: list[datetime | None] | None = None,
    settings: Settings,
) -> list[AuthenticityResult]:
    """Score a batch of reviews.

    1. Call score_single for each review (sequential — no asyncio.gather).
    2. Run batch_signals.score_batch on all texts+dates.
    3. Merge batch flags into each result.
    4. Record AUTHENTICITY_DUPLICATE_CLUSTER_TOTAL if near-duplicate pairs found.

    Returns list of AuthenticityResult in same order as input.
    """
    texts = [text for text, _stars in reviews]

    # Step 1: score each review individually
    single_results: list[AuthenticityResult] = []
    for text, stars in reviews:
        result = await score_single(text, stars=stars, settings=settings)
        single_results.append(result)

    # Step 2: batch signals (near-duplicates, bursts)
    batch_flags: dict[int, list[AuthenticityFlag]] = _score_batch_signals(texts, dates)

    # Count duplicate clusters for metrics
    duplicate_indices: set[int] = {
        idx for idx, flags in batch_flags.items() if AuthenticityFlag.NEAR_DUPLICATE in flags
    }
    if duplicate_indices:
        AUTHENTICITY_DUPLICATE_CLUSTER_TOTAL.inc()

    # Step 3: merge batch flags into each result
    final_results: list[AuthenticityResult] = []
    for idx, result in enumerate(single_results):
        extra_flags = batch_flags.get(idx, [])
        if not extra_flags:
            final_results.append(result)
            continue

        existing: set[AuthenticityFlag] = set(result.flags)
        new_flags: list[AuthenticityFlag] = list(result.flags)
        for flag in extra_flags:
            if flag not in existing:
                existing.add(flag)
                new_flags.append(flag)
                AUTHENTICITY_FLAG_TOTAL.labels(flag=flag.value).inc()

        final_results.append(result.model_copy(update={"flags": new_flags}))

    return final_results
