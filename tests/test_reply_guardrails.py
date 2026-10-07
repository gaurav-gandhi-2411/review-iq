from __future__ import annotations

import pytest
from app.core.reply.guardrails import (
    check_grounded,
    check_language_match,
    check_length,
    check_no_fabrication,
    redact_invented_details,
    run_guardrails,
)

# ---------------------------------------------------------------------------
# check_no_fabrication
# ---------------------------------------------------------------------------


def test_fabrication_full_refund() -> None:
    result = check_no_fabrication("We will give you a full refund immediately")
    assert result is not None
    assert "fabricated commitment" in result


def test_fabrication_process_replacement() -> None:
    # Matches pattern: "we will [verb] ... replacement"
    result = check_no_fabrication("We will process a replacement for you")
    assert result is not None


def test_fabrication_refund_will_be_processed_and_timeline() -> None:
    # Hits both the passive-refund pattern AND the timeline pattern.
    text = "Refund will be processed within 2 days"
    result = check_no_fabrication(text)
    # At least one pattern fires — the function returns on first match.
    assert result is not None


def test_fabrication_guarantee() -> None:
    result = check_no_fabrication("We guarantee a full replacement")
    assert result is not None


def test_fabrication_i_promise() -> None:
    result = check_no_fabrication("I promise to make this right")
    assert result is not None


def test_fabrication_percent_off() -> None:
    result = check_no_fabrication("We offer 20% off on your next purchase")
    assert result is not None


def test_fabrication_no_questions_asked() -> None:
    result = check_no_fabrication("No questions asked return policy")
    assert result is not None


def test_fabrication_free_replacement() -> None:
    result = check_no_fabrication("We offer a free replacement")
    assert result is not None


def test_clean_reply_contact_us() -> None:
    text = (
        "Thank you for your feedback. We are sorry to hear about this. "
        "Please contact us and we will look into this for you."
    )
    assert check_no_fabrication(text) is None


def test_clean_reply_escalated() -> None:
    text = "We understand your frustration and have escalated this to our team."
    assert check_no_fabrication(text) is None


def test_clean_reply_reach_out() -> None:
    text = "Please reach out to us so we can assist you."
    assert check_no_fabrication(text) is None


# ---------------------------------------------------------------------------
# check_language_match
# ---------------------------------------------------------------------------


def test_language_match_en_pass() -> None:
    text = "Thank you for your feedback. We are sorry to hear about your experience."
    assert check_language_match(text, "en") is None


def test_language_mismatch_en_expected_hi() -> None:
    text = "Thank you for your feedback. We appreciate your review."
    result = check_language_match(text, "hi")
    assert result is not None
    assert "language mismatch" in result


def test_language_match_hindi_devanagari() -> None:
    # Devanagari script → detect_language returns "hi"; expected "hi" → pass.
    text = "यह उत्पाद बहुत अच्छा है"
    assert check_language_match(text, "hi") is None


def test_language_mismatch_en_expected_hi_en() -> None:
    text = "Thank you for your feedback. We appreciate your review."
    result = check_language_match(text, "hi-en")
    assert result is not None
    assert "language mismatch" in result


def test_language_match_hindi_expected_hi_en_compatible() -> None:
    # Devanagari → "hi"; expected "hi-en"; {hi, hi-en} → compatible → None.
    text = "यह उत्पाद बहुत अच्छा है"
    assert check_language_match(text, "hi-en") is None


def test_language_match_hinglish_expected_hi_compatible() -> None:
    # Strong Hinglish markers → detect_language returns "hi-en".
    # expected="hi"; {hi, hi-en} → compatible → None.
    text = "bahut bura product hai, bilkul nahi lena"
    assert check_language_match(text, "hi") is None


# ---------------------------------------------------------------------------
# check_length
# ---------------------------------------------------------------------------


def test_length_too_short_two_chars() -> None:
    result = check_length("ok")
    assert result is not None
    assert "too short" in result


def test_length_too_long_2001_chars() -> None:
    result = check_length("x" * 2001)
    assert result is not None
    assert "too long" in result


def test_length_pass_typical_reply() -> None:
    text = "Thank you for your feedback. We will look into this."
    assert check_length(text) is None


def test_length_exactly_at_min() -> None:
    # 30 chars — exactly at minimum; should pass.
    assert check_length("a" * 30) is None


def test_length_exactly_at_max() -> None:
    # 2000 chars — exactly at maximum; should pass.
    assert check_length("a" * 2000) is None


def test_length_one_below_min() -> None:
    # 29 chars — one below minimum; should fail.
    result = check_length("a" * 29)
    assert result is not None
    assert "too short" in result


# ---------------------------------------------------------------------------
# check_grounded
# ---------------------------------------------------------------------------


def test_grounded_keyword_present() -> None:
    # "battery" appears in reply — should pass.
    reply = "We are sorry to hear about the battery performance issue with your device."
    assert check_grounded(reply, ["battery drains fast"], ["battery life"], "en") is None


def test_grounded_generic_reply_no_keywords() -> None:
    # Generic reply with no tokens from "battery drains fast" — should fail.
    reply = (
        "Thank you for reaching out to us. We appreciate your review and will pass your feedback."
    )
    result = check_grounded(reply, ["battery drains fast"], [], "en")
    assert result is not None
    assert "ungrounded" in result


def test_grounded_skipped_for_hindi() -> None:
    # Grounding check is not enforced for vernacular languages.
    reply = "Thank you for reaching out to us. We appreciate your review."
    assert check_grounded(reply, ["battery drains fast"], ["battery life"], "hi") is None


def test_grounded_no_cons_no_topics() -> None:
    # Nothing to ground against — always passes.
    reply = "Thank you for your feedback."
    assert check_grounded(reply, [], [], "en") is None


def test_grounded_skipped_for_hi_en() -> None:
    # Not enforced for hi-en either.
    reply = "Thank you for reaching out to us. We appreciate your review."
    assert check_grounded(reply, ["poor packaging"], [], "hi-en") is None


def test_grounded_battery_in_reply_passes() -> None:
    reply = "We are sorry to hear about the battery performance issue. We have noted your concern."
    assert check_grounded(reply, ["battery drains fast"], [], "en") is None


# ---------------------------------------------------------------------------
# run_guardrails
# ---------------------------------------------------------------------------


def test_run_guardrails_fabrication_violation() -> None:
    violations = run_guardrails(
        "We will give you a full refund",
        expected_language="en",
        cons=["poor quality"],
        topics=["quality"],
    )
    assert len(violations) > 0
    assert any("fabricated commitment" in v for v in violations)


def test_run_guardrails_all_pass() -> None:
    reply = (
        "Thank you for your feedback. We are sorry to hear about the packaging issue. "
        "Please contact our support team and we will assist you further."
    )
    violations = run_guardrails(
        reply,
        expected_language="en",
        cons=["poor packaging"],
        topics=[],
    )
    assert violations == []


# ---------------------------------------------------------------------------
# Hindi / Hinglish commitments + fault admission (caveat-level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Hum aapka product theek kar denge, chinta mat karein",
        "Maaf kijiye, galti hamari thi aur hum sudhaar karenge",
        "Aapka refund mil jayega bahut jaldi",
        "Hum 3 din ke andar replacement bhej denge",
        "हम इसे ठीक कर देंगे, आप चिंता न करें",
        "आपका रिफंड मिल जाएगा",
        "गलती हमारी थी, हमें खेद है",
    ],
)
def test_fabrication_hindi_hinglish_commitments(text: str) -> None:
    assert check_no_fabrication(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Aapka feedback note kar liya hai, humse baat karein",
        "आपकी बात हमने नोट कर ली है, कृपया संपर्क करें",
    ],
)
def test_fabrication_hindi_hinglish_benign(text: str) -> None:
    assert check_no_fabrication(text) is None


# ---------------------------------------------------------------------------
# redact_invented_details -- shapes of the N4c failures (synthetic text)
# ---------------------------------------------------------------------------

REVIEW = "The blender stopped working after two weeks. Very disappointed."


def test_invented_email_replaced_with_placeholder() -> None:
    reply = "Sorry to hear that. Please write to support@example.com for help."
    out, found = redact_invented_details(reply, [REVIEW])
    assert "support@example.com" not in out
    assert "[your support contact]" in out
    assert found == [("email", "support@example.com")]


def test_invented_commitment_shape_is_still_caveated() -> None:
    # Second N4c failure shape: unsupported commitment. Detection is caveat-level.
    assert check_no_fabrication("We will send you a full refund right away.") is not None


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ("Call us on +91 98765 43210 anytime.", "phone"),
        ("Visit www.shop-help.example/returns to proceed.", "url"),
        ("Write to https://help.fakeshop.com/ticket now.", "url"),
        ("We have logged your ticket #A48213 already.", "order id"),
        ("Your order number 5567123 is being checked.", "order id"),
        ("We can refund Rs. 1,499 once verified.", "amount"),
        ("A credit of ₹250 will follow.", "amount"),
    ],
)
def test_invented_details_by_kind(reply: str, kind: str) -> None:
    out, found = redact_invented_details(reply, [REVIEW])
    assert [k for k, _ in found] == [kind]
    assert out != reply and "[" in out


def test_echoed_details_from_review_are_allowed() -> None:
    review = (
        "Order #778812 arrived broken. I emailed help@myshop.in and called 98765 43210. "
        "I paid Rs. 1,499 on www.myshop.in"
    )
    reply = (
        "Sorry about order #778812. We saw you wrote to help@myshop.in, "
        "called 98765 43210 and paid Rs. 1,499 via www.myshop.in."
    )
    out, found = redact_invented_details(reply, [review])
    assert out == reply
    assert found == []


def test_signature_contact_is_allowed() -> None:
    sig = "Team Acme, care@acme.in"
    out, found = redact_invented_details(f"Thanks for writing.\n{sig}", [REVIEW, sig])
    assert found == []
    assert sig in out


def test_non_details_not_flagged() -> None:
    reply = "It stopped after 2 weeks, rated 4.5 stars on 12-10-2026 in 2026 and 3 days."
    out, found = redact_invented_details(reply, [REVIEW])
    assert out == reply
    assert found == []
