"""Unit tests for eval/score_held_out_corpus_v2.py::summarize (constant-field headline + CI)."""

from __future__ import annotations

from statistics import mean
from typing import Any

from eval.bootstrap import bootstrap_ci
from eval.score_held_out_corpus_v2 import summarize


def _record(rid: str, sentiment: float, topics: float) -> dict[str, Any]:
    cond = {
        "field_scores": {"stars": 1.0, "sentiment": sentiment, "topics": topics},
        "overall_score": (1.0 + sentiment + topics) / 3,
        "overall_score_strict": (1.0 + sentiment + topics) / 3,
    }
    return {
        "id": rid,
        "gt_language": "hi-en",
        "detected_language": "hi-en",
        "as_deployed": cond,
        "language_forced": cond,
    }


RECORDS = [
    _record("a", 1.0, 0.5),
    _record("b", 0.0, 1.0),
    _record("c", 1.0, 1.0),
    _record("d", 0.0, 0.0),
]


def test_constant_field_is_detected_and_excluded() -> None:
    out = summarize(RECORDS)
    assert out["constant_fields"] == ["stars"]
    per_record = [
        (r["as_deployed"]["field_scores"]["sentiment"] + r["as_deployed"]["field_scores"]["topics"])
        / 2
        for r in RECORDS
    ]
    assert out["overall_score_excluding_constant_fields"]["as_deployed"] == mean(per_record)
    # The all-fields overall is higher: the constant 1.0 flatters it.
    assert out["as_deployed"]["overall_score"] > mean(per_record)


def test_excluding_ci_is_bootstrap_over_per_record_means_seed_42() -> None:
    out = summarize(RECORDS)
    per_record = [
        (r["as_deployed"]["field_scores"]["sentiment"] + r["as_deployed"]["field_scores"]["topics"])
        / 2
        for r in RECORDS
    ]
    lo, hi = bootstrap_ci(per_record)  # defaults: 10,000 resamples, seed 42
    ci = out["overall_score_excluding_constant_fields_ci_95"]["as_deployed"]
    assert (ci["lower"], ci["upper"], ci["n"]) == (lo, hi, len(RECORDS))
    assert set(out["overall_score_excluding_constant_fields_ci_95"]) == {
        "as_deployed",
        "language_forced",
    }
    assert (
        ci["lower"] <= out["overall_score_excluding_constant_fields"]["as_deployed"] <= ci["upper"]
    )


def _lang_record(
    rid: str, detected: str, gt: str, sentiment: float, topics: float
) -> dict[str, Any]:
    """Two-condition record with a `language` field that is 100% by echo when forced."""
    lang_as_deployed = 1.0 if detected == gt else 0.0

    def cond(language: float) -> dict[str, Any]:
        scores = {"stars": 1.0, "language": language, "sentiment": sentiment, "topics": topics}
        return {
            "field_scores": scores,
            "overall_score": sum(scores.values()) / len(scores),
            "overall_score_strict": sum(scores.values()) / len(scores),
        }

    return {
        "id": rid,
        "gt_language": gt,
        "detected_language": detected,
        "as_deployed": cond(lang_as_deployed),
        "language_forced": cond(1.0),
    }


LANG_RECORDS = [
    _lang_record("a", "en", "hi-en", 1.0, 0.5),
    _lang_record("b", "hi-en", "hi-en", 0.0, 1.0),
    _lang_record("c", "en", "hi-en", 1.0, 1.0),
    _lang_record("d", "en", "en", 0.0, 0.0),
]


def test_headline_excludes_stars_and_language_and_matches_hand_computation() -> None:
    out = summarize(LANG_RECORDS)
    assert out["constant_fields"] == ["stars"]  # language is not constant as deployed
    assert out["headline_echo_fields"] == ["language"]
    assert out["headline_fields"] == ["sentiment", "topics"]
    per_record = [
        (r["as_deployed"]["field_scores"]["sentiment"] + r["as_deployed"]["field_scores"]["topics"])
        / 2
        for r in LANG_RECORDS
    ]
    assert out["overall_score_headline"]["as_deployed"] == mean(per_record)
    # Same predictions in both conditions here, so the forced headline equals it: the echoed
    # `language` field no longer creates a gap between the conditions.
    assert out["overall_score_headline"]["language_forced"] == mean(per_record)
    # The stars-only figure still counts language, so it differs between the conditions.
    excl = out["overall_score_excluding_constant_fields"]
    assert excl["language_forced"] > excl["as_deployed"]


def test_headline_ci_is_bootstrap_10000_seed_42_over_per_record_means() -> None:
    out = summarize(LANG_RECORDS)
    per_record = [
        (r["as_deployed"]["field_scores"]["sentiment"] + r["as_deployed"]["field_scores"]["topics"])
        / 2
        for r in LANG_RECORDS
    ]
    lo, hi = bootstrap_ci(per_record)  # defaults: 10,000 resamples, seed 42
    ci = out["overall_score_headline_ci_95"]["as_deployed"]
    assert (ci["lower"], ci["upper"], ci["n"]) == (lo, hi, len(LANG_RECORDS))


def test_language_label_agreement_replaces_the_misnamed_accuracy_key() -> None:
    out = summarize(LANG_RECORDS)
    assert "language_detection_accuracy" not in out
    assert out["language_label_agreement"] == 0.5  # 2 of 4 detected == gt
    ci = out["language_label_agreement_wilson_95"]
    assert ci["n"] == 4 and ci["lower"] < 0.5 < ci["upper"]
    assert out["language_label_alpha"] == 0.38
    assert "quoted" in out["language_label_alpha_source"]


def test_no_language_field_means_no_echo_exclusion() -> None:
    out = summarize(RECORDS)  # records without a `language` field
    assert out["headline_echo_fields"] == []
    assert out["headline_fields"] == ["sentiment", "topics"]
    assert (
        out["overall_score_headline"]["as_deployed"]
        == out["overall_score_excluding_constant_fields"]["as_deployed"]
    )
