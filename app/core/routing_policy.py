"""Routing policy — pure functions with no I/O.

All functions are deterministic and side-effect-free so they can be
unit-tested without mocking any LLM or provider.
"""

from __future__ import annotations

from typing import Literal

from app.core.language import DetectedLanguage
from app.core.schemas import ReviewExtractionLLMOutput

Tier = Literal["small", "large"]

# Default confidence threshold below which we escalate to the large model.
CONFIDENCE_ESCALATION_THRESHOLD: float = 0.6

# Star ratings considered low (1-2) or high (4-5).
_LOW_STARS_CEILING = 2
_HIGH_STARS_FLOOR = 4


def choose_tier(language: DetectedLanguage) -> Tier:
    """Return the initial model tier for the given detected language.

    All languages — including hi and hi-en — now start on the small model.
    The escalation_triggers mechanism (schema_validation_failed / low_confidence /
    signal_mismatch) handles promotion to the large model only when genuinely
    needed, conserving the scarce free-tier 70B TPD budget for hard cases.

    Carried-debt fix: the previous "hi must bypass small" rationale was based on
    a routed eval where escalation triggers were never firing (0 escalated).  The
    root cause was that triggers were not yet wired for the hi bucket, not that
    the small model is fundamentally unfit for hi.  Now that triggers are tuned,
    bypassing small wastes the large-model daily quota on easy vernacular cases.
    """
    return "small"


def escalation_triggers(
    extraction: ReviewExtractionLLMOutput | None,
    *,
    schema_valid: bool = True,
    confidence_threshold: float = CONFIDENCE_ESCALATION_THRESHOLD,
    input_stars: int | None = None,
) -> list[str]:
    """Return the list of escalation reasons for a small-model response.

    An empty list means no escalation is needed.
    A non-empty list means the router should retry with the large model.

    Triggers (checked in order):
    1. schema_validation_failed — the small model's raw output could not be
       parsed into a valid ReviewExtractionLLMOutput.  When this fires,
       further checks are skipped (there is no extraction to inspect).
    2. low_confidence — extraction.confidence < confidence_threshold.
    3. signal_mismatch — explicit star rating conflicts with sentiment polarity:
       stars <= 2 with positive sentiment, or stars >= 4 with negative sentiment.
       Checked against both extraction.stars (LLM-extracted) and input_stars
       (explicitly provided by the caller from the request context).

    Args:
        extraction: Parsed small-model output, or None when schema_valid=False.
        schema_valid: False when the small model's raw output was not parseable.
        confidence_threshold: Escalate when confidence is below this value.
        input_stars: Explicit star rating from the request payload (e.g. a form
            star widget), if the caller has it.  Optional.
    """
    if not schema_valid:
        return ["schema_validation_failed"]

    if extraction is None:
        return []

    reasons: list[str] = []

    # Trigger 2: low confidence
    if extraction.confidence is not None and extraction.confidence < confidence_threshold:
        reasons.append(f"low_confidence:{extraction.confidence:.2f}")

    # Trigger 3: signal mismatch
    sentiment = extraction.sentiment
    if sentiment in ("positive", "negative"):
        for stars_value, source in [
            (extraction.stars, "extracted"),
            (input_stars, "input"),
        ]:
            if stars_value is None:
                continue
            if stars_value <= _LOW_STARS_CEILING and sentiment == "positive":
                reasons.append(f"signal_mismatch({source}):stars={stars_value},sentiment=positive")
            elif stars_value >= _HIGH_STARS_FLOOR and sentiment == "negative":
                reasons.append(f"signal_mismatch({source}):stars={stars_value},sentiment=negative")

    return reasons


def should_escalate(
    extraction: ReviewExtractionLLMOutput | None,
    *,
    schema_valid: bool = True,
    confidence_threshold: float = CONFIDENCE_ESCALATION_THRESHOLD,
    input_stars: int | None = None,
) -> bool:
    """Convenience wrapper — True when any escalation trigger fires."""
    return bool(
        escalation_triggers(
            extraction,
            schema_valid=schema_valid,
            confidence_threshold=confidence_threshold,
            input_stars=input_stars,
        )
    )
