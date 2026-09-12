"""Measure app.core.grounding's false-positive rate before trusting it -- Session 14 P4c.

Zero quota: reuses the model's own `as_deployed` predictions already recorded in
eval/results/held_out_scoring_v2.json against the 106-item held-out corpus's real
review texts. No new LLM calls.

Checks the same case-insensitive-substring grounding logic against FOUR fields --
competitor_mentions (the one actually wired into the extraction pipeline) plus
pros/cons/topics (checked here ONLY to produce the evidence for why they're excluded,
per app/core/grounding.py's docstring) -- so the scoping decision has a real measurement
behind it, not just a plausibility argument.

Usage:
    uv run python eval/measure_grounding_check.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.grounding import ungrounded_competitor_mentions

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
SCORING_PATH = REPO_ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUTPUT_PATH = REPO_ROOT / "eval" / "results" / "grounding_check_fpr_n106.json"

CHECKED_FIELDS = ("competitor_mentions", "pros", "cons", "topics")


def main() -> int:
    scoring = json.loads(SCORING_PATH.read_text(encoding="utf-8"))
    records_by_id = {r["id"]: r for r in scoring["records"]}

    per_field: dict[str, dict[str, int]] = {
        f: {"n_values_checked": 0, "n_flagged": 0} for f in CHECKED_FIELDS
    }
    flagged_examples: dict[str, list[dict[str, object]]] = {f: [] for f in CHECKED_FIELDS}

    for fixture_path in sorted(FIXTURES_DIR.glob("hien-*.json")):
        fx = json.loads(fixture_path.read_text(encoding="utf-8"))
        rec = records_by_id.get(fx["id"])
        if rec is None:
            continue
        predicted = rec["as_deployed"]["predicted"]
        text = fx["review_text"]

        for field in CHECKED_FIELDS:
            values = predicted.get(field) or []
            per_field[field]["n_values_checked"] += len(values)
            flagged = ungrounded_competitor_mentions(text, values)
            per_field[field]["n_flagged"] += len(flagged)
            for v in flagged:
                flagged_examples[field].append({"id": fx["id"], "text": text, "value": v})

    result = {
        "source_scoring": str(SCORING_PATH.relative_to(REPO_ROOT)),
        "method": "case-insensitive substring match of each list value against review_text",
        "per_field": {
            f: {
                **per_field[f],
                "false_positive_rate": (
                    round(per_field[f]["n_flagged"] / per_field[f]["n_values_checked"], 4)
                    if per_field[f]["n_values_checked"]
                    else None
                ),
            }
            for f in CHECKED_FIELDS
        },
        "flagged_examples": flagged_examples,
    }
    OUTPUT_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    for f in CHECKED_FIELDS:
        p = result["per_field"][f]
        print(
            f"  {f}: {p['n_flagged']}/{p['n_values_checked']} flagged (FPR={p['false_positive_rate']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
