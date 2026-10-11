from __future__ import annotations

import json

from engine.labelling import agreement as AG
from engine.labelling import prompts_v3 as P3


def _raw(**kw: object) -> str:
    base = {"needs_action": "yes", "broad_intent": "product_issue", "sentiment": "negative",
            "urgency": "medium", "aspects": {}}  # fmt: skip
    base.update(kw)
    return json.dumps(base)


def test_parse_accepts_valid_and_rejects_bad_enums() -> None:
    ok, _ = P3.parse_text(_raw(), "beauty", "it broke")
    assert ok is not None and ok["needs_action"] == "yes" and ok["broad_intent"] == "product_issue"
    for bad in (
        _raw(needs_action="maybe"),
        _raw(broad_intent="pricing"),  # a v2 class is not a v3 value
        _raw(sentiment="meh"),
        _raw(urgency="urgent"),
        "not json",
    ):
        assert P3.parse_text(bad, "beauty", "it broke")[0] is None


def test_aspect_needs_a_verifiable_quote_and_apparel_has_none() -> None:
    asp = {"texture": {"sentiment": "positive", "quote": "so smooth"}}
    got, dropped = P3.parse_text(_raw(aspects=asp), "beauty", "It feels SO  smooth on skin")
    assert got["aspects"] == {"texture": "positive"} and dropped == 0
    got, dropped = P3.parse_text(_raw(aspects=asp), "beauty", "nothing about it")
    assert got["aspects"] == {} and dropped == 1
    got, _ = P3.parse_text(_raw(aspects={"fit_size": {"sentiment": "positive", "quote": "fits well"}}),
                           "apparel", "fits well")  # fmt: skip
    assert got["aspects"] == {}  # apparel aspects dropped in Amendment 4


def test_prompt_never_shows_stars_and_hash_is_stable() -> None:
    p = P3.text_prompt("great", "food")
    assert "star" not in p.lower() and "explicit_no_repurchase" not in p
    assert P3.prompt_hash() == P3.prompt_hash()
    assert P3.PROMPT_VERSION == "ri-judge-v3"


def test_aspect_sentiment_is_scored_only_where_two_judges_quoted(monkeypatch) -> None:  # noqa: ANN001
    def rec(aspects: dict) -> dict:
        return {"category": "beauty", "stratum": "beauty",
                "text": {"needs_action": "no", "broad_intent": "praise", "sentiment": "positive",
                         "urgency": "low", "aspects": aspects}}  # fmt: skip

    judges = ["a", "b", "c", "d"]
    items = {
        "i1": {
            "a": rec({"texture": "positive"}),
            "b": rec({"texture": "positive"}),
            "c": rec({}),
            "d": rec({}),
        },  # fmt: skip
        "i2": {
            "a": rec({"value": "negative"}),
            "b": rec({}),
            "c": rec({}),
            "d": rec({}),
        },  # 1 judge: skipped
    }
    aspect_rows: list[list] = []
    real = AG.task_report

    def spy(ratings: list, levels: list, ordinal: bool = False) -> dict:
        if levels == ["positive", "negative", "neutral"]:  # the aspect-sentiment level set
            aspect_rows.extend(ratings)
        return real(ratings, levels, ordinal)

    monkeypatch.setattr(AG, "task_report", spy)
    out = AG.build_tasks(judges, items, "v3")
    assert set(out) >= {"C1_needs_action", "C2_broad_intent", "T2_sentiment", "T3_urgency",
                        "T6_aspect_sentiment_beauty"}  # fmt: skip
    # only i1/texture (two judges quoted) is scored; i2/value (one judge) is not
    assert aspect_rows == [["positive", "positive", None, None]]
