"""Unit tests for eval/heldout_short_sweep.py (Session 17, W3f): exposure matching suited to the
shortest held-out reviews, which the 40-character-window sweep (ADR 0032 V1e) could not cover."""

from __future__ import annotations

import json
from pathlib import Path

from eval.heldout_exposure import held_out_exposure, normalize_review_text
from eval.heldout_short_sweep import (
    SHORT_CUTOFF,
    match_segment,
    segments,
    short_reviews,
    sweep,
)

ROOT = Path(__file__).resolve().parent.parent.parent


def _key(text: str) -> str:
    return normalize_review_text(text)


def test_exact_after_normalization() -> None:
    assert match_segment(_key("Mast product READ MORE"), _key("mast product!")) == "exact"


def test_containment_needs_a_minimum_length() -> None:
    # 3-character key must not "match" every longer string that happens to contain it.
    assert match_segment(_key("ok"), _key("the product is ok i guess")) is None
    assert match_segment(_key("Good product"), _key("Good product, will buy again")) == "contains"
    assert (
        match_segment(_key("Good product, will buy again"), _key("Good product")) == "contained_by"
    )


def test_one_word_edit_is_a_near_duplicate_not_a_window_match() -> None:
    # Different by one word: a 40-char window cannot see it (the strings are shorter than 40).
    assert match_segment(
        _key("bekar hai auto disconnected"), _key("bekar hai auto disconnect")
    ) in {
        "contains",
        "contained_by",
        "near_dup",
    }
    assert match_segment(
        _key("Nice h betray backup mast h"), _key("Nice h battery backup mast h")
    ) == ("near_dup")


def test_unrelated_short_strings_do_not_match() -> None:
    assert match_segment(_key("Bakwaas product"), _key("Superb earphone sound ekdum mast")) is None


def test_segments_cut_a_document_into_lines_fragments_and_quoted_strings() -> None:
    doc = (
        'Review: <review>Ekdum mast product hai bhai! Bahut accha.</review>\nOutput: "mast product"'
    )
    segs = segments(doc)
    assert doc in segs
    assert "Ekdum mast product hai bhai" in [s.strip().split("<review>")[-1] for s in segs]
    assert "mast product" in segs  # the quoted string


def test_sweep_reports_the_shortest_matching_segment_per_source() -> None:
    held = {"h1": "Mast productREAD MORE"}
    sources = {"prompt_text": [("p.py", "intro line\nExample: Ekdum mast product hai bhai\nend")]}
    hits = sweep(held, sources)["h1"]
    assert len(hits) == 1
    assert hits[0]["match"] == "contains"
    assert hits[0]["segment"].startswith("Example") or "mast product" in hits[0]["segment"]
    assert hits[0]["generic"] is True  # every word is generic vocabulary


def test_sweep_returns_an_empty_list_for_a_clean_review() -> None:
    assert sweep({"h": "zzzz qqqq unique"}, {"s": [("a", "nothing alike here at all")]}) == {
        "h": []
    }


def test_short_reviews_uses_the_v1e_cutoff() -> None:
    fx = {
        "a": {"review_text": "short one"},
        "b": {"review_text": "a considerably longer review that clears thirty characters easily"},
    }
    assert list(short_reviews(fx)) == ["a"]
    assert SHORT_CUTOFF == 30


# ---- the committed corpus and artifact ------------------------------------------------------


def test_committed_corpus_has_exactly_17_short_reviews() -> None:
    assert len(short_reviews()) == 17


def test_committed_sweep_artifact_reproduces_and_finds_no_new_exposure() -> None:
    """Pins the W3f conclusion: no short review is an exact or near-duplicate of anything the
    development process can see, other than the one the exposure ledger already holds.

    If this fails, a short review now matches a dev-visible text: decide whether it is exposure
    (add it to eval/heldout_exposure_ack.json AND make the scorer exclude it) before editing the
    test.
    """
    from eval.heldout_short_sweep import run

    committed = json.loads(
        (ROOT / "eval" / "results" / "heldout_short_sweep.json").read_text(encoding="utf-8")
    )
    assert run() == committed  # deterministic and not hand-edited
    exposed = set(held_out_exposure())
    for fid, rec in committed["reviews"].items():
        strong = [h for h in rec["hits"] if h["match"] in {"exact", "near_dup"}]
        if fid in exposed:
            continue  # already excluded from the headline
        assert not strong, f"{fid} newly matches a development-visible text: {strong}"
        # A non-generic containment is also a finding to look at, not to wave through.
        assert all(h["generic"] for h in rec["hits"]), f"{fid}: non-generic containment hit"
