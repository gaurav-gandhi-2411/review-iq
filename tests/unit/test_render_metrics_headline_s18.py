"""S18 D1/D2: the published held-out headline copy is rendered from artifacts, the per-field
range is computed, the narrowed Manski interval is never user-facing, the "mid-70s" clause is
guarded, and `pros` is reported separately."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from scripts.render_metrics import (
    MID_SEVENTIES_BAND,
    MidSeventiesGuardError,
    mid_seventies_clause,
    render_held_out_table_md,
)

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


HELD = _load("eval/results/held_out_scoring_v2.json")
PANEL2 = _load("eval/consensus/results/panel2_silver.json")
PROS_GOLD = _load("eval/results/pros_gold_analysis.json")


def _headline(md: str) -> str:
    return next(line for line in md.splitlines() if line.startswith("**") and "unseen" in line)


def _render(held: dict[str, Any] = HELD, panel2: dict[str, Any] = PANEL2) -> str:
    return render_held_out_table_md(held, panel2, PROS_GOLD)


def test_headline_copy_is_exactly_the_approved_wording_from_artifacts() -> None:
    cell = HELD["headline_grid"][HELD["headline_policy"]["cell"]]
    pub = cell["as_deployed"]
    un = HELD["unscored"]
    nb = PANEL2["narrowed_bounds"]
    vals = [
        round(v["mean_score_on_resolved_vs_silver"] * 100)
        for v in nb["per_field"].values()
        if v["mean_score_on_resolved_vs_silver"] is not None
    ]
    expected = (
        f"**{pub['score'] * 100:.1f}% [{pub['ci_95']['lower'] * 100:.1f}%, "
        f"{pub['ci_95']['upper'] * 100:.1f}%] on {cell['n']} unseen reviews, scored only where the "
        f"three-judge panel agreed. {un['unscored_fraction'] * 100:.1f}% of field-pairs "
        f"({un['n_pairs_unscored']} of {un['n_pairs']}) had no consensus and are unscored. "
        "Those are the hardest cases: on the "
        f"{nb['n_resolved_by_panel2']} of them a second, independent LLM panel could settle, the "
        f"model scores {min(vals)}-{max(vals)}% depending on the field. So overall accuracy is "
        "probably lower than the headline, in roughly the mid-70s (exploratory, LLM-judged, "
        "not human-verified).**"
    )
    assert _headline(_render()) == expected


def test_range_is_min_max_of_rendered_per_field_values_not_a_constant() -> None:
    panel2 = copy.deepcopy(PANEL2)
    pf = panel2["narrowed_bounds"]["per_field"]
    pf["product"]["mean_score_on_resolved_vs_silver"] = 0.40
    pf["cons"]["mean_score_on_resolved_vs_silver"] = 0.80
    line = _headline(_render(panel2=panel2))
    assert "scores 40-80% depending on the field" in line


def test_resolved_count_comes_from_the_artifact() -> None:
    panel2 = copy.deepcopy(PANEL2)
    panel2["narrowed_bounds"]["n_resolved_by_panel2"] = 31
    assert "on the 31 of them a second" in _headline(_render(panel2=panel2))


def test_mismatched_panel2_and_headline_artifacts_fail_loudly() -> None:
    panel2 = copy.deepcopy(PANEL2)
    panel2["narrowed_bounds"]["n_pairs_unscored_by_panel1"] = 59
    with pytest.raises(ValueError, match="disagree on the unscored pairs"):
        _render(panel2=panel2)


def test_narrowed_manski_interval_is_not_in_the_rendered_block_readme_or_site() -> None:
    nb = PANEL2["narrowed_bounds"]
    lo = f"{nb['narrowed_manski_lower_unresolved_all_wrong']['score'] * 100:.1f}"
    hi = f"{nb['narrowed_manski_upper_unresolved_all_correct']['score'] * 100:.1f}"
    texts = [_render()] + [
        (ROOT / rel).read_text(encoding="utf-8")
        for rel in ("README.md", "site/index.html", "site/docs/index.html")
    ]
    for text in texts:
        assert f"[{lo}%, {hi}%]" not in text
        assert f"[{lo}, {hi}]" not in text
        # the narrowed bounds are also never printed one at a time as a bound
        assert not re.search(rf"narrowed[^\n]{{0,80}}({re.escape(lo)}|{re.escape(hi)})%", text)


def test_mid_seventies_clause_is_printed_only_inside_the_band() -> None:
    lo, hi = MID_SEVENTIES_BAND
    assert (lo, hi) == (0.730, 0.780)
    ev = {"a": 0.743, "b": 0.75, "c": 0.756, "narrowed lower bound": 0.737}
    clause, ok = mid_seventies_clause(ev, 0.7805, 0.796)
    assert ok
    assert clause == "in roughly the mid-70s"


@pytest.mark.parametrize(
    ("evidence", "silver", "headline"),
    [
        ({"a": 0.70, "b": 0.75}, 0.76, 0.796),  # a judge-sensitivity value below the band
        ({"a": 0.79, "b": 0.75}, 0.76, 0.796),  # above the band
        ({"a": 0.75, "b": 0.75}, 0.72, 0.796),  # silver estimate below the band floor
        ({"a": 0.75, "b": 0.75}, 0.80, 0.796),  # silver estimate not below the headline
        ({}, 0.76, 0.796),  # no evidence at all is not evidence
    ],
)
def test_mid_seventies_guard_degrades_the_clause_when_inputs_leave_the_band(
    evidence: dict[str, float], silver: float, headline: float
) -> None:
    clause, ok = mid_seventies_clause(evidence, silver, headline)
    assert not ok
    assert clause == "lower than the headline"


def test_render_fails_loudly_when_the_guard_fails() -> None:
    held = copy.deepcopy(HELD)
    js = held["unscored"]["judge_sensitivity"]["scored_against_each_judge"]
    next(iter(js.values()))["score"] = 0.66
    with pytest.raises(MidSeventiesGuardError, match="no longer supported"):
        _render(held=held)


def test_pros_is_reported_separately_and_headline_scorer_is_disclosed() -> None:
    md = _render()
    b = HELD["pros_soft_recall"]
    line = next(x for x in md.splitlines() if x.startswith("**`pros` reported separately"))
    rec = b["soft_recall"]
    assert f"soft recall {rec['score'] * 100:.1f}%" in line
    assert f"on {b['n_scored']} resolved gold pairs" in line
    assert "threshold 0.5, pre-registered" in line
    assert ("V-a: FAIL" if not b["validation"]["V_a"]["pass"] else "V-a: PASS") in line
    assert ("V-b: FAIL" if not b["validation"]["V_b"]["pass"] else "V-b: PASS") in line
    sweep = PROS_GOLD["score_sweep_unexposed_70"]["pros"]
    assert f"{sweep['swing_max_minus_min'] * 100:.1f} points across matcher thresholds" in line
    assert "still scores `pros` with the existing token-level F1 scorer" in line
    if not b["validation"]["validated"]:
        assert "NOT validated" in line
