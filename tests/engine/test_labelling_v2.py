from __future__ import annotations

from engine.labelling import mine
from engine.labelling import prompts_v2 as P2

REVIEW = "The zip broke after two days and the fabric feels cheap, but the colour is lovely."


def _raw(aspects: dict) -> str:
    import json

    return json.dumps(
        {
            "primary_intent": "product_defect",
            "secondary_intents": ["praise", "bogus"],
            "sentiment": "mixed",
            "urgency": "medium",
            "explicit_no_repurchase": "no",
            "aspects": aspects,
        }
    )


def test_aspect_without_a_verifiable_quote_is_dropped_and_counted() -> None:
    good = {"sentiment": "negative", "quote": "the fabric feels cheap"}
    invented = {"sentiment": "positive", "quote": "the stitching is perfect"}
    out, dropped = P2.parse_text(
        _raw({"fabric_quality": good, "style": invented, "fit_size": "negative"}), "apparel", REVIEW
    )
    assert out is not None
    assert out["aspects"] == {"fabric_quality": "negative"}
    assert dropped == 2  # the invented quote and the bare-string value
    assert out["secondary_intents"] == ["praise"]


def test_quote_match_ignores_case_and_whitespace_but_needs_real_words() -> None:
    q = {"sentiment": "positive", "quote": "THE   colour is   lovely"}
    out, dropped = P2.parse_text(_raw({"colour_accuracy": q}), "apparel", REVIEW)
    assert out["aspects"] == {"colour_accuracy": "positive"} and dropped == 0
    tiny = {"sentiment": "positive", "quote": "a"}
    out2, dropped2 = P2.parse_text(_raw({"colour_accuracy": tiny}), "apparel", REVIEW)
    assert out2["aspects"] == {} and dropped2 == 1


def test_v2_enums_are_strict_and_vernacular_has_no_aspects() -> None:
    assert P2.parse_text("not json", "food", REVIEW) == (None, 0)
    bad = _raw({}).replace('"explicit_no_repurchase": "no"', '"explicit_no_repurchase": "maybe"')
    assert P2.parse_text(bad, "food", REVIEW) == (None, 0)
    out, _ = P2.parse_text(
        _raw({"taste": {"sentiment": "negative", "quote": "feels cheap"}}), "vernacular", REVIEW
    )
    assert out["aspects"] == {}


def test_v2_prompt_states_the_priority_rule_never_shows_stars_and_has_no_mismatch_call() -> None:
    p = P2.text_prompt("it broke", "beauty")
    assert "highest-priority" in p and "NOT praise" in p
    assert "star" not in p.lower() and "mismatch" not in p.lower()
    assert "results_efficacy" in p and "shade_match" not in p  # five aspects, shade_match dropped
    assert P2.PRIORITY[0] == "return_refund_request" and P2.PRIORITY[-1] == "praise"
    assert "previous answer was not valid" in P2.text_prompt("x", "food", retry=True)


def test_mining_patterns_hit_obvious_cases_and_miss_plain_praise() -> None:
    assert mine.matches("It arrived late and the courier was rude", "delivery")
    assert mine.matches("Does it come with a charger?", "question")
    assert mine.matches("I want a refund for this", "return_refund_request")
    assert mine.matches("Gave me a rash on my cheeks", "high_urgency")
    assert not any(mine.matches("Love it, works great", c) for c in mine.PATTERNS)
