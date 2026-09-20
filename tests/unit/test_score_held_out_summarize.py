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
