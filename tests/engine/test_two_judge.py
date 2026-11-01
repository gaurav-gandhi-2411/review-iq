from __future__ import annotations

from engine.labelling import two_judge as TJ


def rec(i: str = "x", aspects: dict | None = None, **kw: str) -> dict:
    base = {
        "needs_action": "no",
        "broad_intent": "praise",
        "sentiment": "positive",
        "urgency": "low",
    }
    base.update(kw)
    return {"id": i, "text": {**base, "aspects": aspects or {}}}


FAILED = {"id": "x", "text": None}


def test_pairwise_rule_edges() -> None:
    assert TJ.two_judge_label("a", "a") == ("a", "agree")
    assert TJ.two_judge_label("a", "a", "b") == ("a", "agree")  # mistral never overrides agreement
    assert TJ.two_judge_label("a", "b", "b") == ("b", "tiebreak")
    assert TJ.two_judge_label("a", "b", "a") == ("a", "tiebreak")
    assert TJ.two_judge_label("a", "b", "c") == (None, "abstain")  # a third opinion is not a label
    assert TJ.two_judge_label("a", "b") == (None, "abstain")  # mistral not run
    assert TJ.two_judge_label("a", None, "a") == ("a", "tiebreak")  # one judge failed to parse
    assert TJ.two_judge_label("a", None, "b") == (None, "abstain")
    assert TJ.two_judge_label(None, None, "a") == (None, "abstain")
    assert TJ.two_judge_label("a", None) == (None, "abstain")  # a lone answer is never a label


def test_needs_tiebreak_only_when_a_task_disagrees_or_is_missing() -> None:
    assert not TJ.needs_tiebreak(rec(), rec())
    assert TJ.needs_tiebreak(rec(), rec(urgency="medium"))
    assert TJ.needs_tiebreak(rec(), FAILED)
    assert TJ.needs_tiebreak(FAILED, FAILED)
    assert TJ.needs_tiebreak(None, rec())
    # aspects quoted by only one judge do NOT trigger mistral; a two-judge conflict does
    assert not TJ.needs_tiebreak(rec(aspects={"taste": "positive"}), rec())
    assert not TJ.needs_tiebreak(
        rec(aspects={"taste": "positive"}), rec(aspects={"taste": "positive"})
    )
    assert TJ.needs_tiebreak(rec(aspects={"taste": "positive"}), rec(aspects={"taste": "negative"}))


def test_label_item_agree_tiebreak_abstain_per_task() -> None:
    a = rec(urgency="low", sentiment="positive", broad_intent="praise")
    b = rec(urgency="medium", sentiment="negative", broad_intent="praise")
    m = rec(urgency="medium", sentiment="mixed", broad_intent="other")
    out = TJ.label_item(a, b, m)
    assert out["broad_intent"] == {"label": "praise", "how": "agree"}
    assert out["urgency"] == {"label": "medium", "how": "tiebreak"}
    assert out["sentiment"] == {"label": None, "how": "abstain"}
    assert TJ.label_item(a, b)["urgency"]["how"] == "abstain"  # mistral absent


def test_aspect_labels() -> None:
    a = rec(aspects={"taste": "positive", "value": "negative", "only_a": "neutral"})
    b = rec(aspects={"taste": "positive", "value": "positive"})
    assert TJ.label_aspects(a, b) == {"taste": "positive"}
    m = rec(aspects={"value": "negative", "only_a": "neutral"})
    assert TJ.label_aspects(a, b, m) == {"taste": "positive", "value": "negative"}
    assert TJ.label_aspects(a, FAILED) == {}


def test_tiebreak_ids_keeps_order_and_handles_missing_records() -> None:
    a = {"1": rec(), "2": rec(), "3": rec()}
    b = {"1": rec(), "2": rec(urgency="high"), "4": rec()}
    assert TJ.tiebreak_ids(a, b, ["3", "2", "1"]) == ["3", "2"]  # 3: judge b has no record


def test_sealed_consensus_rule() -> None:
    four = [rec(), rec(), rec(), rec(urgency="high")]
    out = TJ.sealed_label_item(four)
    assert out["urgency"] == "low" and out["sentiment"] == "positive"  # 3 of 4
    split = [rec(), rec(), rec(urgency="medium"), rec(urgency="high")]
    assert TJ.sealed_label_item(split)["urgency"] is None
    two_failed = [rec(), rec(), FAILED, FAILED]
    assert TJ.sealed_label_item(two_failed)["sentiment"] is None  # fewer than 3 valid judges
    asp = [rec(aspects={"taste": "positive"})] * 3 + [rec()]
    assert TJ.sealed_label_item(asp)["aspects"] == {"taste": "positive"}
    asp1 = [rec(aspects={"taste": "positive"}), rec(), rec(), rec()]
    assert TJ.sealed_label_item(asp1)["aspects"] == {}  # one quoter only
