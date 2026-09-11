"""Session 7 P2: classify why the model abstains, using the consensus panel as adjudicator.

Cross-references eval/results/latest.json (the 49-fixture CI-gate set) against
eval/consensus/results/consensus_labels.jsonl's validate-mode entries (the blind 2-judge
panel recovered by PR #140) to answer, per hedge-tracked field: is a given abstention
genuinely ambiguous (the panel also can't commit, or splits), or decidable-but-hedged
(the panel converges on an answer the model declined to give)?

Excludes the degenerate case where the hedge value IS the ground truth (e.g. sentiment:
"mixed" when the review really is mixed) -- that's a correct answer, not an abstention, and
counting it as one is exactly the bug this script exists to catch. See
docs/specs/wave1-coverage-abstention-analysis.md for the full write-up of what this found.

Zero live calls: reads only already-committed cassette/consensus JSON.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "eval" / "results" / "latest.json"
CONSENSUS_LABELS_PATH = REPO_ROOT / "eval" / "consensus" / "results" / "consensus_labels.jsonl"

# The only two hedge-tracked fields in this repo's established coverage methodology
# (eval/README.md) and the value each one hedges to.
HEDGE_VALUE: dict[str, Any] = {"buy_again": None, "sentiment": "mixed"}


def load_consensus_by_id() -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    with CONSENSUS_LABELS_PATH.open(encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry["mode"] == "validate":
                by_id[entry["id"]] = entry
    return by_id


def classify_abstentions() -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    """Return (real_hedge_rows, already_correct) for every hedge-tracked field."""
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    consensus_by_id = load_consensus_by_id()

    real_hedges: list[dict[str, Any]] = []
    already_correct: list[tuple[str, str, str]] = []

    for fixture in results["fixtures"]:
        fixture_id, language = fixture["id"], fixture["language"]
        consensus = consensus_by_id.get(fixture_id, {}).get("consensus", {})
        for field_result in fixture["fields"]:
            field = field_result["field"]
            if field not in HEDGE_VALUE:
                continue
            hedge_value = HEDGE_VALUE[field]
            if field_result["predicted"] != hedge_value:
                continue  # model committed to an answer -- not a hedge
            if field_result["expected"] == hedge_value:
                # The hedge value IS the ground truth: a correct answer, not an abstention.
                already_correct.append((field, language, fixture_id))
                continue

            field_consensus = consensus.get(field, {})
            silver = field_consensus.get("silver")
            agreement = field_consensus.get("agreement")
            panel_committed = silver != hedge_value and silver is not None
            panel_decisive = panel_committed and agreement in ("unanimous", "majority")
            real_hedges.append(
                {
                    "id": fixture_id,
                    "language": language,
                    "field": field,
                    "model_predicted": field_result["predicted"],
                    "expected_ground_truth": field_result["expected"],
                    "panel_silver": silver,
                    "panel_agreement": agreement,
                    "panel_votes": field_consensus.get("votes", {}),
                    "classification": (
                        "decidable_but_hedged" if panel_decisive else "genuinely_ambiguous"
                    ),
                }
            )
    return real_hedges, already_correct


def main() -> None:
    real_hedges, already_correct = classify_abstentions()

    already_correct_by_field: dict[str, int] = defaultdict(int)
    for field, _language, _fixture_id in already_correct:
        already_correct_by_field[field] += 1

    print("=== Metric-definition check: hedge value == ground truth (not a real abstention) ===")
    for field, n in already_correct_by_field.items():
        print(f"{field}: {n} cases miscounted as abstention by naive null/mixed coverage")

    print()
    print("=== Real hedges only: panel-adjudicated decidability ===")
    summary: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in real_hedges:
        for key in ((row["field"], "ALL"), (row["field"], row["language"])):
            summary[key]["n"] += 1
            summary[key][row["classification"]] += 1

    for (field, language), counts in sorted(summary.items()):
        n = counts["n"]
        decidable = counts.get("decidable_but_hedged", 0)
        ambiguous = counts.get("genuinely_ambiguous", 0)
        print(
            f"{field:12s} {language:6s} n={n:3d}  "
            f"decidable_but_hedged={decidable} ({decidable / n:.1%})  "
            f"genuinely_ambiguous={ambiguous} ({ambiguous / n:.1%})"
        )


if __name__ == "__main__":
    main()
