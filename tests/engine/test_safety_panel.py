from __future__ import annotations

from engine.labelling import safety_panel as SP


def _rec(urg: str) -> dict:
    return {"text": {"needs_action": "yes", "broad_intent": "product_issue", "sentiment": "negative",
                     "urgency": urg, "aspects": {}}}  # fmt: skip


def test_precision_per_keyword_and_split() -> None:
    manifest = {
        "keywords": ["burn", "rash"],
        "items": [
            {"id": "a", "src": "flipkart", "sha": "1", "kw": ["burn"], "split": "train", "hinglish": False},
            {"id": "b", "src": "flipkart", "sha": "2", "kw": ["burn", "rash"], "split": "test", "hinglish": True},
            {"id": "c", "src": "food", "sha": "3", "kw": ["rash"], "split": "train", "hinglish": False},
        ],
    }  # fmt: skip
    votes = {
        "a": ["high"] * 4,
        "b": ["high", "high", "high", "low"],
        "c": ["low", "medium", "high", "low"],
    }
    items = {i: dict(zip(SP.PANEL, [_rec(u) for u in us], strict=True)) for i, us in votes.items()}
    rep, labels = SP.build_report(manifest, items)
    assert rep["per_keyword"]["burn"]["high"]["k"] == 2  # a and b
    assert rep["per_keyword"]["rash"]["high"]["k"] == 1  # b only; c has no consensus
    assert rep["per_keyword"]["rash"]["no_consensus"] == 1
    assert rep["positives_by_split"] == {"train": 1, "val": 0, "test": 1}
    assert rep["hinglish"]["hinglish"]["n_candidates"] == 1
    assert all("text" not in x for x in labels)


def test_hinglish_regex() -> None:
    assert SP.HINGLISH.search("phone mein aag lag gayi")
    assert not SP.HINGLISH.search("it caught fire")
