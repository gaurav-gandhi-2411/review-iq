from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.authenticity.batch_signals import (
    detect_burst,
    find_near_duplicates,
    score_batch,
)
from app.core.authenticity.heuristics import (
    compute_heuristic_score,
    score_brevity,
    score_incentivized_phrases,
    score_rating_text_mismatch,
    score_repetition,
)
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel, AuthenticityResult

# ---------------------------------------------------------------------------
# schema.py tests
# ---------------------------------------------------------------------------


def test_from_signals_no_llm_score_uses_heuristic() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=0.75,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="some review text",
    )
    assert result.score == 0.75


def test_from_signals_blends_scores() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=1.0,
        llm_score=0.0,
        flags=[],
        reasons="",
        review_text="blended review",
    )
    # llm_score=0.0 < 0.65 → combined = min(blended=0.4, llm_score=0.0) = 0.0
    # LLM cap overrides clean heuristic when LLM signals maximum suspicion.
    assert abs(result.score - 0.0) < 1e-9


def test_from_signals_score_clamped_below_zero() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=-5.0,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="clamped low",
    )
    assert result.score == 0.0


def test_from_signals_score_clamped_above_one() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=5.0,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="clamped high",
    )
    assert result.score == 1.0


def test_label_genuine() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=0.70,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="genuine review text",
    )
    assert result.label == AuthenticityLabel.GENUINE


def test_label_suspicious() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=0.50,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="suspicious review text",
    )
    assert result.label == AuthenticityLabel.SUSPICIOUS


def test_label_likely_fake() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=0.20,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="likely fake review text",
    )
    assert result.label == AuthenticityLabel.LIKELY_FAKE


def test_review_hash_is_64_char_hex() -> None:
    result = AuthenticityResult.from_signals(
        heuristic_score=0.8,
        llm_score=None,
        flags=[],
        reasons="",
        review_text="hash me please",
    )
    assert len(result.review_hash) == 64
    assert all(c in "0123456789abcdef" for c in result.review_hash)


# ---------------------------------------------------------------------------
# heuristics.py tests
# ---------------------------------------------------------------------------


def test_incentivized_phrase_detected() -> None:
    penalty, flagged = score_incentivized_phrases("I received free sample for this review")
    assert flagged is True
    assert penalty == 0.8


def test_incentivized_phrase_not_detected() -> None:
    penalty, flagged = score_incentivized_phrases("This is a great product")
    assert flagged is False
    assert penalty == 0.0


def test_brevity_flagged_short() -> None:
    penalty, flagged = score_brevity("Good product")
    assert flagged is True
    assert penalty == 0.6


def test_brevity_not_flagged_long() -> None:
    penalty, flagged = score_brevity(
        "This product is absolutely fantastic and I have been using it for months with great results"
    )
    assert flagged is False
    assert penalty == 0.0


def test_repetition_flagged() -> None:
    penalty, flagged = score_repetition("good good good good good good good good")
    assert flagged is True
    assert penalty > 0.0


def test_repetition_not_flagged() -> None:
    penalty, flagged = score_repetition(
        "The battery life is exceptional and the camera quality is superb"
    )
    assert flagged is False


def test_mismatch_high_stars_negative_text() -> None:
    text = "This product is bad and terrible and horrible and awful and disappointing"
    penalty, flagged = score_rating_text_mismatch(text, stars=5)
    assert flagged is True
    assert penalty == 0.7


def test_mismatch_low_stars_positive_text() -> None:
    text = "This product is great and amazing and excellent and awesome and fantastic"
    penalty, flagged = score_rating_text_mismatch(text, stars=1)
    assert flagged is True
    assert penalty == 0.7


def test_no_mismatch_positive_text_high_stars() -> None:
    text = "This product is great and amazing and excellent"
    penalty, flagged = score_rating_text_mismatch(text, stars=5)
    assert flagged is False
    assert penalty == 0.0


def test_compute_heuristic_score_incentivized() -> None:
    text = "I received free sample for this review. The product was okay but nothing special."
    score, flags = compute_heuristic_score(text)
    assert score < 0.5
    assert AuthenticityFlag.INCENTIVIZED_PHRASE in flags


def test_compute_heuristic_score_genuine() -> None:
    text = (
        "I have been using this blender for three months now. "
        "The motor is powerful and it handles frozen fruit without any trouble. "
        "The cleaning is simple and the build quality feels solid. "
        "Highly recommended for daily smoothies."
    )
    score, flags = compute_heuristic_score(text, stars=5)
    assert score >= 0.6
    # No flags expected for a clearly genuine review
    assert AuthenticityFlag.INCENTIVIZED_PHRASE not in flags
    assert AuthenticityFlag.EXCESSIVE_BREVITY not in flags


# ---------------------------------------------------------------------------
# batch_signals.py tests
# ---------------------------------------------------------------------------


def test_find_near_duplicates_similar_pair() -> None:
    t1 = "the quick brown fox jumps over the lazy dog"
    t2 = "the quick brown fox jumps over the lazy cat"
    pairs = find_near_duplicates([t1, t2], threshold=0.60)
    assert len(pairs) == 1
    i, j, sim = pairs[0]
    assert i == 0 and j == 1
    assert sim >= 0.60


def test_find_near_duplicates_different_texts() -> None:
    t1 = "battery life is exceptional and camera quality is superb"
    t2 = "delivery was late and packaging was broken on arrival today"
    pairs = find_near_duplicates([t1, t2], threshold=0.60)
    assert pairs == []


def test_detect_burst_with_dense_dates() -> None:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    # 7 reviews on consecutive days; a 3-day window captures 4 per anchor point.
    # min_count=4 is reliably satisfied for the first four anchor dates.
    dates: list[datetime | None] = [base + timedelta(days=i) for i in range(7)]
    bursts = detect_burst(dates, window_days=3, min_count=4)
    assert len(bursts) >= 1
    # At least one burst window contains >= 4 reviews
    assert any(count >= 4 for _, _, count in bursts)


def test_detect_burst_all_none() -> None:
    dates: list[datetime | None] = [None, None, None]
    bursts = detect_burst(dates)
    assert bursts == []


def test_score_batch_near_duplicate_flags_both() -> None:
    t1 = "the quick brown fox jumps over the lazy dog"
    t2 = "the quick brown fox jumps over the lazy cat"
    t3 = "completely different text about something else entirely unrelated"
    result = score_batch([t1, t2, t3], duplicate_threshold=0.60)
    assert AuthenticityFlag.NEAR_DUPLICATE in result[0]
    assert AuthenticityFlag.NEAR_DUPLICATE in result[1]
    assert AuthenticityFlag.NEAR_DUPLICATE not in result[2]


# --- New phrase coverage ---


def test_score_incentivized_gift_sample() -> None:
    _, flagged = score_incentivized_phrases("got this as a gift sample to review")
    assert flagged is True


def test_score_incentivized_review_program() -> None:
    _, flagged = score_incentivized_phrases("part of a review program run by the brand")
    assert flagged is True


def test_score_incentivized_for_review_purposes() -> None:
    _, flagged = score_incentivized_phrases("received product free for review purposes")
    assert flagged is True


def test_score_incentivized_discounted_rate_to_write() -> None:
    _, flagged = score_incentivized_phrases("provided at a discounted rate to write a review")
    assert flagged is True


# --- Blend logic: LLM suspicion must not be overridden by clean heuristics ---


def test_blend_suspicious_llm_caps_composite() -> None:
    """LLM says 0.55 (suspicious), heuristics clean (1.0).
    New blend: min(0.73, 0.55) = 0.55 → suspicious.
    Old blend would give 0.73 → genuine (the bug we fixed).
    """
    result = AuthenticityResult.from_signals(
        heuristic_score=1.0,
        llm_score=0.55,
        flags=[],
        reasons="test",
        review_text="test review",
        model_used="test-model",
        llm_signal_ok=True,
    )
    assert result.label == AuthenticityLabel.SUSPICIOUS
    assert result.score == pytest.approx(0.55)


def test_blend_genuine_llm_uses_normal_blend() -> None:
    """LLM says 0.85 (genuine), heuristics clean (1.0).
    blended = 0.4×1.0 + 0.6×0.85 = 0.91. Since llm >= 0.65, no cap.
    """
    result = AuthenticityResult.from_signals(
        heuristic_score=1.0,
        llm_score=0.85,
        flags=[],
        reasons="test",
        review_text="test review",
        model_used="test-model",
        llm_signal_ok=True,
    )
    assert result.label == AuthenticityLabel.GENUINE
    assert result.score == pytest.approx(0.91)


def test_blend_clean_heuristics_borderline_llm() -> None:
    """LLM says 0.60 (top of suspicious band), heuristics clean.
    min(0.4×1.0 + 0.6×0.60, 0.60) = min(0.76, 0.60) = 0.60 → suspicious.
    """
    result = AuthenticityResult.from_signals(
        heuristic_score=1.0,
        llm_score=0.60,
        flags=[],
        reasons="test",
        review_text="test review",
        model_used="test-model",
        llm_signal_ok=True,
    )
    assert result.label == AuthenticityLabel.SUSPICIOUS
    assert result.score == pytest.approx(0.60)
