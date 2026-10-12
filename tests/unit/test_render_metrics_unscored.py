"""Session 17 (W3b): the published held-out headline carries its unscored fraction beside it, and
the coverage/known-gaps figures say they are over unseen reviews. Rendered from the committed
artifacts, so a hand-typed number cannot satisfy these."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.render_metrics import (
    render_committed_accuracy_headline_md,
    render_coverage_metrics_table_html,
    render_coverage_metrics_table_md,
    render_held_out_table_md,
    render_known_gaps_html,
)

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "eval" / "results" / name).read_text(encoding="utf-8"))


HELD = _load("held_out_scoring_v2.json")


def _headline_line(md: str) -> str:
    return next(line for line in md.splitlines() if line.startswith("**") and "unseen" in line)


def test_headline_sentence_carries_score_ci_n_and_unscored_fraction_together() -> None:
    line = _headline_line(render_held_out_table_md(HELD))
    pub = HELD["headline_grid"][HELD["headline_policy"]["cell"]]["as_deployed"]
    un = HELD["unscored"]
    assert f"{pub['score'] * 100:.1f}%" in line
    assert f"[{pub['ci_95']['lower'] * 100:.1f}%, {pub['ci_95']['upper'] * 100:.1f}%]" in line
    assert f"on {un['n_reviews']} unseen reviews" in line
    assert "only where the three-judge panel agreed." in line
    assert f"{un['unscored_fraction'] * 100:.1f}% of field-pairs" in line
    assert f"({un['n_pairs_unscored']} of {un['n_pairs']})" in line
    assert line.endswith("(exploratory, LLM-judged, not human-verified).**")


def test_unscored_cell_is_in_the_headline_table_row_not_a_footnote() -> None:
    md = render_held_out_table_md(HELD)
    row = next(line for line in md.splitlines() if line.startswith("| **As actually deployed**"))
    un = HELD["unscored"]
    assert f"{un['n_pairs_unscored']} of {un['n_pairs']}" in row


def test_per_field_unscored_table_lists_every_headline_field_and_the_bounds() -> None:
    md = render_held_out_table_md(HELD)
    for f in HELD["headline_fields"]:
        assert any(line.startswith(f"| `{f}` | ") and " of 70 " in line for line in md.splitlines())
    assert "No assumption-free point estimate of overall accuracy is identifiable" in md
    assert "a bound, not a result" in md
    lo = HELD["unscored"]["bounds"]["lower_unscored_all_wrong"]["score"]
    hi = HELD["unscored"]["bounds"]["upper_unscored_all_correct"]["score"]
    assert f"[{lo * 100:.1f}%, {hi * 100:.1f}%]" in md


def test_judge_sensitivity_is_labelled_as_an_unverified_assumption() -> None:
    md = render_held_out_table_md(HELD)
    assert "an assumption that is not verified" in md
    assert "not an estimate" in md


def test_artifact_without_the_unscored_block_fails_loudly_not_silently() -> None:
    broken = {k: v for k, v in HELD.items() if k != "unscored"}
    with pytest.raises(KeyError):
        render_held_out_table_md(broken)


COVERAGE = _load("coverage_metrics_n106.json")


def test_coverage_table_is_over_unseen_reviews_and_keeps_the_all_reviews_figures() -> None:
    md = render_coverage_metrics_table_md(COVERAGE)
    n = COVERAGE["unexposed"]["n_fixtures"]
    assert f"n={n} reviews the prompt-development process had not seen" in md
    assert f"Over all {COVERAGE['n_fixtures']}" in md
    assert "so including them flattered it" in md
    assert "6/50" in md  # unseen sentiment wrong-committed, not the all-106 10/82


def test_coverage_html_and_headline_use_the_unseen_cut() -> None:
    html = render_coverage_metrics_table_html(COVERAGE)
    assert 'colspan="4"' in html and "had not seen" in html
    headline = render_committed_accuracy_headline_md(COVERAGE)
    assert f"n={COVERAGE['unexposed']['n_fixtures']} unseen reviews" in headline


def test_coverage_artifact_without_the_unexposed_block_renders_the_legacy_basis() -> None:
    legacy = {k: v for k, v in COVERAGE.items() if k not in {"unexposed", "exposed"}}
    assert "unseen" not in render_coverage_metrics_table_md(legacy)
    assert "n=106" in render_coverage_metrics_table_md(legacy)


def test_known_gaps_sentence_states_its_scope_when_the_artifact_does() -> None:
    data = _load("known_gaps_n106.json")
    html = render_known_gaps_html(data)
    assert "had not seen" in html and "judge panel split" in html
    legacy = {**data, "short_reviews": data["short_reviews_pre_s17_all_reviews_all_pairs"]}
    assert "had not seen" not in render_known_gaps_html(legacy)
