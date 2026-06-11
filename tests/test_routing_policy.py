"""Unit tests for app.core.routing_policy — pure function coverage.

No mocking: all functions are deterministic and tested in isolation.
"""

from __future__ import annotations

from app.core.routing_policy import (
    CONFIDENCE_ESCALATION_THRESHOLD,
    choose_tier,
    escalation_triggers,
    should_escalate,
)
from app.core.schemas import ReviewExtractionLLMOutput, Sentiment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extraction(
    *,
    sentiment: Sentiment | None = None,
    stars: int | None = None,
    confidence: float | None = None,
) -> ReviewExtractionLLMOutput:
    """Build a minimal ReviewExtractionLLMOutput with only the fields under test set."""
    return ReviewExtractionLLMOutput(
        sentiment=sentiment,
        stars=stars,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# choose_tier
# ---------------------------------------------------------------------------


class TestChooseTier:
    def test_en_returns_small(self) -> None:
        assert choose_tier("en") == "small"

    def test_hi_returns_large(self) -> None:
        assert choose_tier("hi") == "large"

    def test_hi_en_returns_large(self) -> None:
        assert choose_tier("hi-en") == "large"

    def test_other_returns_small(self) -> None:
        assert choose_tier("other") == "small"


# ---------------------------------------------------------------------------
# escalation_triggers — no escalation
# ---------------------------------------------------------------------------


class TestEscalationTriggersNoEscalation:
    def test_high_confidence_neutral_no_stars(self) -> None:
        ext = _extraction(confidence=0.95, sentiment=Sentiment.neutral)
        assert escalation_triggers(ext) == []

    def test_stars5_positive_consistent(self) -> None:
        ext = _extraction(stars=5, sentiment=Sentiment.positive, confidence=0.9)
        assert escalation_triggers(ext) == []

    def test_stars1_negative_consistent(self) -> None:
        ext = _extraction(stars=1, sentiment=Sentiment.negative, confidence=0.9)
        assert escalation_triggers(ext) == []

    def test_stars3_negative_boundary(self) -> None:
        # stars=3 is not >= 4, so no mismatch
        ext = _extraction(stars=3, sentiment=Sentiment.negative, confidence=0.9)
        assert escalation_triggers(ext) == []

    def test_stars3_positive_boundary(self) -> None:
        # stars=3 is not <= 2, so no mismatch
        ext = _extraction(stars=3, sentiment=Sentiment.positive, confidence=0.9)
        assert escalation_triggers(ext) == []


# ---------------------------------------------------------------------------
# escalation_triggers — schema_validation_failed
# ---------------------------------------------------------------------------


class TestEscalationTriggersSchemaFailed:
    def test_schema_invalid_none_extraction(self) -> None:
        result = escalation_triggers(None, schema_valid=False)
        assert result == ["schema_validation_failed"]

    def test_schema_invalid_stops_immediately(self) -> None:
        # Even if we somehow pass an extraction, schema_valid=False wins immediately
        # and no other triggers are evaluated.
        ext = _extraction(confidence=0.1, stars=1, sentiment=Sentiment.positive)
        result = escalation_triggers(ext, schema_valid=False)
        assert result == ["schema_validation_failed"]
        assert len(result) == 1

    def test_none_extraction_schema_valid_returns_empty(self) -> None:
        # extraction=None but schema_valid=True (edge case) → no triggers
        result = escalation_triggers(None, schema_valid=True)
        assert result == []


# ---------------------------------------------------------------------------
# escalation_triggers — low_confidence
# ---------------------------------------------------------------------------


class TestEscalationTriggersLowConfidence:
    def test_confidence_below_threshold(self) -> None:
        ext = _extraction(confidence=0.55)
        result = escalation_triggers(ext, confidence_threshold=0.6)
        assert any("low_confidence:0.55" in r for r in result)

    def test_confidence_exactly_at_threshold_no_trigger(self) -> None:
        ext = _extraction(confidence=CONFIDENCE_ESCALATION_THRESHOLD)
        result = escalation_triggers(ext)
        assert not any("low_confidence" in r for r in result)

    def test_confidence_above_threshold_no_trigger(self) -> None:
        ext = _extraction(confidence=0.9)
        result = escalation_triggers(ext)
        assert not any("low_confidence" in r for r in result)

    def test_confidence_none_no_trigger(self) -> None:
        ext = _extraction(confidence=None)
        result = escalation_triggers(ext)
        assert not any("low_confidence" in r for r in result)

    def test_confidence_custom_threshold(self) -> None:
        # confidence=0.7 is below threshold=0.8
        ext = _extraction(confidence=0.7)
        result = escalation_triggers(ext, confidence_threshold=0.8)
        assert any("low_confidence:0.70" in r for r in result)


# ---------------------------------------------------------------------------
# escalation_triggers — signal_mismatch (extracted stars)
# ---------------------------------------------------------------------------


class TestEscalationTriggersSignalMismatchExtracted:
    def test_stars1_positive_triggers(self) -> None:
        ext = _extraction(stars=1, sentiment=Sentiment.positive, confidence=0.9)
        result = escalation_triggers(ext)
        assert any("signal_mismatch(extracted):stars=1,sentiment=positive" in r for r in result)

    def test_stars2_positive_triggers(self) -> None:
        ext = _extraction(stars=2, sentiment=Sentiment.positive, confidence=0.9)
        result = escalation_triggers(ext)
        assert any("signal_mismatch(extracted):stars=2,sentiment=positive" in r for r in result)

    def test_stars4_negative_triggers(self) -> None:
        ext = _extraction(stars=4, sentiment=Sentiment.negative, confidence=0.9)
        result = escalation_triggers(ext)
        assert any("signal_mismatch(extracted):stars=4,sentiment=negative" in r for r in result)

    def test_stars5_negative_triggers(self) -> None:
        ext = _extraction(stars=5, sentiment=Sentiment.negative, confidence=0.9)
        result = escalation_triggers(ext)
        assert any("signal_mismatch(extracted):stars=5,sentiment=negative" in r for r in result)


# ---------------------------------------------------------------------------
# escalation_triggers — signal_mismatch (input_stars)
# ---------------------------------------------------------------------------


class TestEscalationTriggersSignalMismatchInput:
    def test_input_stars1_positive_triggers(self) -> None:
        # extraction.stars=None so only input_stars is checked
        ext = _extraction(stars=None, sentiment=Sentiment.positive, confidence=0.9)
        result = escalation_triggers(ext, input_stars=1)
        assert any("signal_mismatch(input):stars=1,sentiment=positive" in r for r in result)
        # extracted source must NOT appear (extraction.stars is None)
        assert not any("signal_mismatch(extracted)" in r for r in result)

    def test_input_stars5_negative_triggers(self) -> None:
        ext = _extraction(stars=None, sentiment=Sentiment.negative, confidence=0.9)
        result = escalation_triggers(ext, input_stars=5)
        assert any("signal_mismatch(input):stars=5,sentiment=negative" in r for r in result)
        assert not any("signal_mismatch(extracted)" in r for r in result)


# ---------------------------------------------------------------------------
# escalation_triggers — multiple triggers
# ---------------------------------------------------------------------------


class TestEscalationTriggersMultiple:
    def test_low_confidence_and_signal_mismatch(self) -> None:
        ext = _extraction(stars=1, sentiment=Sentiment.positive, confidence=0.4)
        result = escalation_triggers(ext)
        assert any("low_confidence" in r for r in result)
        assert any("signal_mismatch(extracted)" in r for r in result)
        assert len(result) >= 2

    def test_both_extracted_and_input_stars_mismatch(self) -> None:
        # extracted stars=1 positive AND input_stars=2 positive → two mismatch entries
        ext = _extraction(stars=1, sentiment=Sentiment.positive, confidence=0.9)
        result = escalation_triggers(ext, input_stars=2)
        assert any("signal_mismatch(extracted):stars=1,sentiment=positive" in r for r in result)
        assert any("signal_mismatch(input):stars=2,sentiment=positive" in r for r in result)


# ---------------------------------------------------------------------------
# escalation_triggers — neutral/mixed sentiment exempt
# ---------------------------------------------------------------------------


class TestEscalationTriggersNeutralMixedExempt:
    def test_stars1_neutral_no_mismatch(self) -> None:
        ext = _extraction(stars=1, sentiment=Sentiment.neutral, confidence=0.9)
        result = escalation_triggers(ext)
        assert not any("signal_mismatch" in r for r in result)

    def test_stars1_mixed_no_mismatch(self) -> None:
        ext = _extraction(stars=1, sentiment=Sentiment.mixed, confidence=0.9)
        result = escalation_triggers(ext)
        assert not any("signal_mismatch" in r for r in result)

    def test_stars5_neutral_no_mismatch(self) -> None:
        ext = _extraction(stars=5, sentiment=Sentiment.neutral, confidence=0.9)
        result = escalation_triggers(ext)
        assert not any("signal_mismatch" in r for r in result)

    def test_stars5_mixed_no_mismatch(self) -> None:
        ext = _extraction(stars=5, sentiment=Sentiment.mixed, confidence=0.9)
        result = escalation_triggers(ext)
        assert not any("signal_mismatch" in r for r in result)


# ---------------------------------------------------------------------------
# should_escalate
# ---------------------------------------------------------------------------


class TestShouldEscalate:
    def test_returns_true_when_triggers_non_empty(self) -> None:
        ext = _extraction(confidence=0.1)
        assert should_escalate(ext) is True

    def test_returns_false_when_triggers_empty(self) -> None:
        ext = _extraction(confidence=0.95, sentiment=Sentiment.neutral)
        assert should_escalate(ext) is False

    def test_schema_invalid_returns_true(self) -> None:
        assert should_escalate(None, schema_valid=False) is True

    def test_high_confidence_positive_stars4_returns_false(self) -> None:
        ext = _extraction(stars=4, sentiment=Sentiment.positive, confidence=0.9)
        assert should_escalate(ext) is False

    def test_mismatch_returns_true(self) -> None:
        ext = _extraction(stars=1, sentiment=Sentiment.positive, confidence=0.9)
        assert should_escalate(ext) is True
