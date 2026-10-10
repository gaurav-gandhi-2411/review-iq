"""S18 D1/D2: the published held-out headline copy is rendered from artifacts, the per-field
range is computed, the narrowed Manski interval is never user-facing (only its lower endpoint
appears, as one end of the stated independent-estimate range), the overall-accuracy range
endpoints are rendered from artifacts and guarded, and `pros` is reported separately."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from scripts.render_metrics import (
    OverallRangeGuardError,
    overall_range_clause,
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
        "probably lower than the headline \u2014 independent estimates range "
        f"{nb['narrowed_manski_lower_unresolved_all_wrong']['score'] * 100:.1f}% to "
        f"{nb['silver_adjudicated_estimate_conditional_on_panel2_agreement']['score'] * 100:.1f}% "
        "(exploratory, LLM-judged, not human-verified).**"
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
        # the narrowed UPPER bound never appears at all; the lower one legitimately appears
        # only as the left endpoint of the stated range ("73.7% to 78.0%"), never as a pair
        assert f"{hi}%" not in text


def test_overall_range_endpoints_are_rendered_from_the_panel2_artifact() -> None:
    nb = PANEL2["narrowed_bounds"]
    lo = nb["narrowed_manski_lower_unresolved_all_wrong"]["score"]
    hi = nb["silver_adjudicated_estimate_conditional_on_panel2_agreement"]["score"]
    pub = HELD["headline_grid"][HELD["headline_policy"]["cell"]]["as_deployed"]["score"]
    assert lo < hi < pub  # the guard's invariant holds on the committed artifacts
    assert f"{lo * 100:.1f}% to {hi * 100:.1f}%" in _headline(_render())
    assert overall_range_clause(0.737, 0.7805, 0.796) == "73.7% to 78.0%"


@pytest.mark.parametrize(
    ("lower", "upper", "headline"),
    [
        (0.78, 0.75, 0.796),  # lower >= upper
        (0.75, 0.75, 0.796),  # degenerate range
        (0.74, 0.80, 0.796),  # upper not strictly below the headline
        (0.74, 0.796, 0.796),  # upper equals the headline
        (0.80, 0.81, 0.796),  # both above the headline
    ],
)
def test_overall_range_guard_fails_when_endpoints_are_inconsistent(
    lower: float, upper: float, headline: float
) -> None:
    with pytest.raises(OverallRangeGuardError, match="inconsistent") as exc:
        overall_range_clause(lower, upper, headline)
    # the message names the offending values
    assert f"{lower * 100:.1f}%" in str(exc.value)
    assert f"{headline * 100:.1f}%" in str(exc.value)


@pytest.mark.parametrize(
    "missing",
    [
        "narrowed_manski_lower_unresolved_all_wrong",
        "silver_adjudicated_estimate_conditional_on_panel2_agreement",
    ],
)
def test_render_fails_when_an_endpoint_is_missing_from_the_artifact(missing: str) -> None:
    panel2 = copy.deepcopy(PANEL2)
    del panel2["narrowed_bounds"][missing]
    with pytest.raises(OverallRangeGuardError, match="missing"):
        _render(panel2=panel2)


def test_render_fails_loudly_when_an_endpoint_reaches_the_headline() -> None:
    panel2 = copy.deepcopy(PANEL2)
    nb = panel2["narrowed_bounds"]
    nb["silver_adjudicated_estimate_conditional_on_panel2_agreement"]["score"] = 0.80
    with pytest.raises(OverallRangeGuardError, match="inconsistent"):
        _render(panel2=panel2)


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
