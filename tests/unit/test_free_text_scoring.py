"""Behavior tests for eval/free_text_scoring.py (Session 15c C2).

The scorer must (a) stop penalizing correct-but-differently-spelled answers, and (b) never
credit a wrong answer. (b) is the contamination risk: a scorer change that only ever raises
scores has to be shown not to raise them by leniency, so the hostile cases matter as much as
the fixes.
"""

from __future__ import annotations

import pytest
from eval.free_text_scoring import (
    canonical_competitor,
    canonical_product,
    canonical_topic,
    product_score,
)
from eval.runner import score_fixture

NULL_SPELLINGS = [
    "unknown",
    "Unknown",
    "product",
    "Product",
    "products",
    "general product",
    "unknown product",
    "this product",
    "unspecified product",
    "  UNKNOWN  ",
    "n/a",
    "none",
    "",
    None,
]


@pytest.mark.parametrize("value", NULL_SPELLINGS)
def test_every_no_product_spelling_is_null(value: object) -> None:
    assert canonical_product(value) is None


@pytest.mark.parametrize("model", ["unknown product", "general product", "this product"])
@pytest.mark.parametrize("labeler", ["unknown", "product", "Product", "products"])
def test_model_and_labeler_placeholders_agree(model: str, labeler: str) -> None:
    """The Q8 finding: independently-written 'no product named' strings must match."""
    assert product_score(model, labeler) == 1.0
    assert product_score(labeler, model) == 1.0


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Headphones", "headphone"),
        ("OnePlus Bluetooth", "Bluetooth oneplus"),
        ("one plus", "oneplus"),
        ("BoAt 235v2 bluetooth headset", "boAt 235v2 bluetooth headset"),
        ("earphones!", "Earphone"),
    ],
)
def test_cosmetic_differences_match(a: str, b: str) -> None:
    assert product_score(a, b) == 1.0
    assert product_score(b, a) == 1.0


@pytest.mark.parametrize(
    ("pred", "gold"),
    [
        ("Mast", "unknown"),  # hallucinated product where none was named
        ("earphone", "headphones"),  # different categories are different products
        ("BassDesignSound", "BassDesign"),  # no substring credit
        ("headset", "Ultimate headset Paisa"),  # no superset credit
        ("OnePlus product", "product"),  # a brand token makes it a named product
        ("general product", "headphones"),  # model abstained where a product was named
        ("headphones", "general product"),
        ("Mast product", "unknown"),
    ],
)
def test_wrong_answers_are_never_credited(pred: str, gold: str) -> None:
    assert product_score(pred, gold) == 0.0
    assert product_score(gold, pred) == 0.0


def test_named_product_containing_a_null_word_is_not_null() -> None:
    assert canonical_product("OnePlus product") is not None
    assert canonical_product("Osm products") is not None


def test_null_vs_none_value() -> None:
    assert product_score(None, "unknown") == 1.0
    assert product_score(None, "headphones") == 0.0


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("battery_life", "battery"),
        ("delivery_speed", "delivery"),
        ("sound_quality", "sound"),
        ("overall_quality", "product_quality"),
        ("product_quality", "quality"),
        ("value_for_money", "value"),
        ("mic", "microphone"),
        ("Sound Quality", "sound_quality"),
    ],
)
def test_topic_structural_equivalences(a: str, b: str) -> None:
    assert canonical_topic(a) == canonical_topic(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("bass", "sound_quality"),
        ("price", "value"),  # a synonym, but structural rules must not invent it
        ("battery", "charging"),
        ("build_quality", "sound_quality"),
        ("quality", "sound_quality"),
    ],
)
def test_topic_distinct_topics_stay_distinct(a: str, b: str) -> None:
    assert canonical_topic(a) != canonical_topic(b)


def test_lone_quality_topic_is_not_erased() -> None:
    assert canonical_topic("quality") == "quality"


def test_competitor_spacing_and_punctuation() -> None:
    assert canonical_competitor("real me") == canonical_competitor("realme")
    assert canonical_competitor("Philip's") == canonical_competitor("philips")
    assert canonical_competitor("boat") != canonical_competitor("boat 225")


def _fixture(product: str, topics: list[str]) -> dict[str, object]:
    return {
        "ground_truth": {"product": product, "topics": topics, "sentiment": "positive"},
        "scoring_notes": {
            "exact_match_fields": ["product", "sentiment"],
            "set_overlap_fields": ["topics"],
        },
    }


def test_score_fixture_strict_reproduces_old_behavior() -> None:
    fx = _fixture("unknown", ["battery"])
    ext = {"product": "unknown product", "topics": ["battery_life"], "sentiment": "positive"}
    strict = {r.field: r.score for r in score_fixture(fx, ext, strict=True)}
    fixed = {r.field: r.score for r in score_fixture(fx, ext)}
    assert strict == {"product": 0.0, "sentiment": 1.0, "topics": 0.0}
    assert fixed == {"product": 1.0, "sentiment": 1.0, "topics": 1.0}


def test_score_fixture_leaves_enum_fields_exact() -> None:
    """Enum fields (sentiment, buy_again, language) must stay exact-match."""
    fx = _fixture("unknown", ["battery"])
    ext = {"product": "unknown", "topics": ["battery"], "sentiment": "mixed"}
    assert {r.field: r.score for r in score_fixture(fx, ext)}["sentiment"] == 0.0


def test_scorer_never_lowers_a_perfect_exact_match() -> None:
    """Anything that matched exactly under the old scorer must still match (monotonic)."""
    for value in ["headphones", "unknown", "Boat Rockerz 400", "product", "OnePlus Bullets"]:
        assert product_score(value, value) == 1.0
