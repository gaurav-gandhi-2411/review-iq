"""Unit tests for Pydantic schemas."""

import pytest
from app.core.schemas import (
    BatchReviewRequest,
    ReviewExtraction,
    ReviewExtractionLLMOutput,
    ReviewRequest,
    Sentiment,
    Urgency,
)
from pydantic import ValidationError


class TestReviewRequest:
    def test_valid_text(self) -> None:
        r = ReviewRequest(text="Great product!")
        assert r.text == "Great product!"

    def test_text_is_stripped(self) -> None:
        r = ReviewRequest(text="  hello  ")
        assert r.text == "hello"

    def test_text_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReviewRequest(text="x" * 5001)

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReviewRequest(text="")

    def test_input_hash_is_deterministic(self) -> None:
        r1 = ReviewRequest(text="same text")
        r2 = ReviewRequest(text="same text")
        assert r1.input_hash() == r2.input_hash()

    def test_input_hash_prefix(self) -> None:
        r = ReviewRequest(text="hello")
        assert r.input_hash().startswith("sha256:")

    def test_different_texts_different_hash(self) -> None:
        r1 = ReviewRequest(text="text A")
        r2 = ReviewRequest(text="text B")
        assert r1.input_hash() != r2.input_hash()


class TestReviewExtraction:
    def _base(self) -> dict:
        return {"product": "Test Vacuum"}

    def test_minimal_valid(self) -> None:
        e = ReviewExtraction(**self._base())
        assert e.product == "Test Vacuum"
        assert e.stars is None
        assert e.pros == []
        assert e.urgency == Urgency.low

    def test_stars_must_be_1_to_5(self) -> None:
        with pytest.raises(ValidationError):
            ReviewExtraction(**self._base(), stars=0)
        with pytest.raises(ValidationError):
            ReviewExtraction(**self._base(), stars=6)

    def test_stars_inferred_must_be_1_to_5(self) -> None:
        with pytest.raises(ValidationError):
            ReviewExtraction(**self._base(), stars_inferred=6)

    def test_language_is_lowercased(self) -> None:
        e = ReviewExtraction(**self._base(), language="EN")
        assert e.language == "en"

    def test_topics_deduplicated(self) -> None:
        e = ReviewExtraction(**self._base(), topics=["battery", "Battery", "BATTERY"])
        assert len(e.topics) == 1

    def test_competitor_mentions_deduplicated(self) -> None:
        e = ReviewExtraction(**self._base(), competitor_mentions=["Dyson", "dyson"])
        assert len(e.competitor_mentions) == 1

    def test_confidence_must_be_0_to_1(self) -> None:
        with pytest.raises(ValidationError):
            ReviewExtraction(**self._base(), confidence=1.5)
        with pytest.raises(ValidationError):
            ReviewExtraction(**self._base(), confidence=-0.1)

    def test_full_turbo_vac_extraction(self) -> None:
        e = ReviewExtraction(
            product="Turbo-Vac 5000",
            stars=None,
            stars_inferred=3,
            pros=["incredible suction", "very quiet operation"],
            cons=["poor battery life (15 minutes)", "fragile plastic handle"],
            buy_again=False,
            sentiment=Sentiment.mixed,
            topics=["suction", "noise", "battery", "build_quality", "price"],
            competitor_mentions=["Dyson"],
            urgency=Urgency.low,
            feature_requests=[],
            language="en",
        )
        assert e.stars is None
        assert e.stars_inferred == 3
        assert e.buy_again is False
        assert e.sentiment == Sentiment.mixed
        assert "Dyson" in e.competitor_mentions


class TestReviewExtractionLLMOutput:
    def test_llm_output_has_no_meta(self) -> None:
        out = ReviewExtractionLLMOutput(product="X")
        assert not hasattr(out, "extraction_meta")

    def test_valid_minimal(self) -> None:
        out = ReviewExtractionLLMOutput(product="Gadget Pro")
        assert out.product == "Gadget Pro"
        assert out.urgency == Urgency.low


class TestBatchReviewRequest:
    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValidationError):
            BatchReviewRequest(reviews=[])

    def test_too_many_reviews_raises(self) -> None:
        reviews = [{"text": f"review {i}"} for i in range(101)]
        with pytest.raises(ValidationError):
            BatchReviewRequest(reviews=reviews)

    def test_valid_batch(self) -> None:
        b = BatchReviewRequest(reviews=[{"text": "good"}, {"text": "bad"}])
        assert len(b.reviews) == 2
