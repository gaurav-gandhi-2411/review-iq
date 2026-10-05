"""Session 17 (W3e): every analysis that scored held-out gold must not score a panel-split default
as a label, and must say which reviews it covers. These pin the recut figures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from eval.analyze_coverage_metrics import analyze
from eval.analyze_known_gaps import analyze_short_reviews
from eval.heldout_exposure import unresolved_fields

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "eval" / "results"


def _load(name: str) -> dict[str, Any]:
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


# ---- known gaps -----------------------------------------------------------------------------


def _fx(rid: str, pros: list[str], text: str = "bad hai") -> dict[str, Any]:
    return {
        "id": rid,
        "review_text": text,
        "ground_truth": {"pros": pros, "sentiment": "negative"},
        "labeling_meta": {"agreement_per_field": {"pros": "split", "sentiment": "unanimous"}},
    }


def _rec(rid: str, exposed: bool = False) -> dict[str, Any]:
    return {
        "id": rid,
        "exposure": ["benchmark_gold"] if exposed else [],
        "unresolved_fields": ["pros"],
        "as_deployed": {
            "predicted": {"pros": ["cheap"], "sentiment": "negative"},
            "field_scores": {"pros": 0.0, "sentiment": 1.0},
        },
    }


def test_split_default_is_not_scored_as_a_label_in_known_gaps() -> None:
    fixtures = [_fx("a", []), _fx("b", [], "also bad")]
    recs = {"a": _rec("a"), "b": _rec("b", exposed=True)}
    legacy = analyze_short_reviews(fixtures, recs)
    # Against the empty default the model's non-empty `pros` reads as a confident wrong answer.
    assert legacy["per_field"]["pros"].get("wrong_committed") == 2
    fixed = analyze_short_reviews(fixtures, recs, exclude_exposed=True, exclude_split=True)
    assert fixed["n_short_reviews"] == 1  # `b` was seen by development
    assert fixed["per_field"]["pros"] == {}  # the pair is unscored, not a wrong commit
    assert fixed["n_split_pairs_excluded"] == 1
    assert fixed["counts"]["wrong_committed"] == 0
    assert fixed["excluded_exposed_reviews"] and fixed["excluded_split_gold_pairs"]


def test_known_gaps_total_checks_counts_only_scored_pairs() -> None:
    fixtures = [_fx("a", [])]
    fixed = analyze_short_reviews(
        fixtures, {"a": _rec("a")}, exclude_exposed=True, exclude_split=True
    )
    from eval.analyze_known_gaps import FIELDS

    assert fixed["total_field_checks"] == len(FIELDS) - 1


def test_committed_known_gaps_publishes_the_exclusion_and_keeps_the_old_cut() -> None:
    d = _load("known_gaps_n106.json")
    pub = d["short_reviews"]
    old = d["short_reviews_pre_s17_all_reviews_all_pairs"]
    assert pub["excluded_exposed_reviews"] and pub["excluded_split_gold_pairs"]
    assert pub["n_split_pairs_excluded"] > 0
    assert old["n_split_pairs_excluded"] == 0 and old["total_field_checks"] == 27 * 12
    assert pub["total_field_checks"] < old["total_field_checks"]
    assert pub["counts"]["wrong_committed"] < old["counts"]["wrong_committed"]


# ---- coverage -------------------------------------------------------------------------------


def test_coverage_unexposed_block_is_the_analysis_over_unseen_reviews_only() -> None:
    held = _load("held_out_scoring_v2.json")
    cov = _load("coverage_metrics_n106.json")
    unseen = [r for r in held["records"] if not r["exposure"]]
    assert cov["unexposed"]["n_fixtures"] == len(unseen) == 70
    assert cov["unexposed"]["per_field"] == json.loads(json.dumps(analyze(unseen)))
    assert cov["n_fixtures"] == 106  # the all-reviews block is kept for the ADRs that cite it
    assert cov["exposed"]["n_fixtures"] + cov["unexposed"]["n_fixtures"] == 106


def test_coverage_fields_have_no_split_pairs_so_only_exposure_changes_them() -> None:
    held = _load("held_out_scoring_v2.json")
    for r in held["records"]:
        assert not {"sentiment", "buy_again"} & set(r["unresolved_fields"]), r["id"]


# ---- scorer delta / routing cost / product null inventory -----------------------------------


def test_scorer_delta_split_excluded_blocks_drop_exactly_the_unresolved_pairs() -> None:
    sd = _load("scorer_delta_n106.json")
    fixtures = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in (ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish").glob("hien-*.json")
    }
    n_prod = sum(1 for f in fixtures.values() if "product" in unresolved_fields(f))
    for cond in ("as_deployed", "language_forced"):
        c = sd["conditions"][cond]
        assert c["per_field_split_excluded"]["product"]["n"] == 106 - n_prod
        assert c["per_field_split_excluded"]["product"]["n_split_pairs_excluded"] == n_prod
        assert c["per_field_split_excluded"]["sentiment"]["n_split_pairs_excluded"] == 0
        assert c["product_attribution_split_excluded"]["n"] == 106 - n_prod
        # The old all-pairs blocks are untouched and still carry every fixture.
        assert c["per_field"]["product"]["strict_mean"] is not None
        assert c["overall_split_excluded"]["delta_ci95"][0] > 0  # comparator effect still real


def test_routing_cost_split_excluded_delta_stays_within_noise() -> None:
    rc = _load("routing_cost_n106.json")
    for cut, n in (("all_106", 106), ("unseen_70", 70)):
        h = rc["headline_split_excluded"][cut]["ex_stars_ex_language"]
        assert h["n"] == n
        lo, hi = h["paired_delta"]["ci95"]
        assert lo < 0 < hi  # no measurable extraction cost of misrouting, as ADR 0021 concluded
    # The pre-existing all-pairs headline is still present for the ADRs that cite it.
    assert "ex_stars_ex_language" in rc["headline"]


def test_product_null_inventory_reports_the_resolved_only_gold() -> None:
    inv = _load("product_null_inventory.json")["sections"]
    assert inv["held_out_gold"]["n"] == 106
    resolved = inv["held_out_gold_resolved_only"]
    assert resolved["n"] < 106
    # Most of the placeholder "unknown" gold values are panel splits, not labels.
    assert resolved["n_null"] < inv["held_out_gold"]["n_null"]
    assert pytest.approx(106 - resolved["n"]) == 28
