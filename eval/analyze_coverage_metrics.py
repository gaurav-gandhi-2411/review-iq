"""P3a (Session 12): coverage, accuracy-on-answered, and wrong-committed at n=106.

Reads the already-recorded held-out scoring output (eval/results/held_out_scoring_v2.json,
produced by eval/score_held_out_corpus_v2.py against committed cassettes -- ZERO quota spent
running this script). Computes, for each hedge-capable field, the Session 6/9 hedge vocabulary
(docs/specs/wave1-coverage-abstention-analysis.md, HEDGE_FIELDS convention from
eval/score_held_out_corpus.py):

  - coverage: fraction of fixtures where the model committed to a non-hedge answer.
  - accuracy_on_answered: mean field score among ONLY the committed (non-hedge) answers.
  - wrong_committed: among committed answers, the count/fraction that scored exactly 0
    (a full miss, not a partial-credit fuzzy mismatch). This is the "rarely wrong when it
    commits" claim's actual evidence -- flat accuracy blends "hedged" and "wrong" into one
    number and cannot support or refute that claim on its own.

All on the "as_deployed" condition (real language routing, ADR 0021) -- the number that
matters for what a customer actually experiences, not the routing-corrected number.

Usage:
    uv run python eval/analyze_coverage_metrics.py
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IN_PATH = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUT_PATH = ROOT / "eval" / "results" / "coverage_metrics_n106.json"

import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from eval.bootstrap import bootstrap_ci  # noqa: E402

# Same convention as eval/score_held_out_corpus.py's HEDGE_FIELDS -- the two fields this
# product's own extraction schema allows to hedge (null / "mixed").
HEDGE_FIELDS: dict[str, Any] = {"sentiment": "mixed", "buy_again": None}


def analyze(records: list[dict[str, Any]], condition: str = "as_deployed") -> dict[str, Any]:
    per_field: dict[str, Any] = {}
    for field, hedge_value in HEDGE_FIELDS.items():
        predicted_vals = [
            r[condition]["predicted"][field]
            for r in records
            if "error" not in r[condition] and field in r[condition]["predicted"]
        ]
        scores = [
            r[condition]["field_scores"][field]
            for r in records
            if "error" not in r[condition] and field in r[condition]["field_scores"]
        ]
        n_total = len(predicted_vals)

        answered_mask = [v != hedge_value for v in predicted_vals]
        n_answered = sum(answered_mask)
        coverage = n_answered / n_total if n_total else None
        coverage_ci = bootstrap_ci([1.0 if a else 0.0 for a in answered_mask]) if n_total else None

        answered_scores = [s for s, a in zip(scores, answered_mask, strict=True) if a]
        accuracy_on_answered = mean(answered_scores) if answered_scores else None
        accuracy_on_answered_ci = bootstrap_ci(answered_scores) if answered_scores else None

        # "Wrong-committed": among committed (answered) predictions, a full miss (score == 0
        # exactly) -- not a partial fuzzy-credit mismatch. Exact-match fields (sentiment,
        # buy_again are both exact-match scored, see eval/runner.py::score_fixture) only ever
        # score 0.0 or 1.0, so this is unambiguous for both fields this script covers.
        wrong_flags = [1.0 if s == 0.0 else 0.0 for s in answered_scores]
        n_wrong = int(sum(wrong_flags))
        wrong_committed_rate = mean(wrong_flags) if wrong_flags else None
        wrong_committed_ci = bootstrap_ci(wrong_flags) if wrong_flags else None

        per_field[field] = {
            "n_total": n_total,
            "n_answered": n_answered,
            "coverage": coverage,
            "coverage_ci_95": (
                {"lower": coverage_ci[0], "upper": coverage_ci[1]} if coverage_ci else None
            ),
            "accuracy_on_answered": accuracy_on_answered,
            "accuracy_on_answered_ci_95": (
                {"lower": accuracy_on_answered_ci[0], "upper": accuracy_on_answered_ci[1]}
                if accuracy_on_answered_ci
                else None
            ),
            "n_wrong_committed": n_wrong,
            "wrong_committed_of_answered": f"{n_wrong}/{n_answered}",
            "wrong_committed_rate": wrong_committed_rate,
            "wrong_committed_rate_ci_95": (
                {"lower": wrong_committed_ci[0], "upper": wrong_committed_ci[1]}
                if wrong_committed_ci
                else None
            ),
        }
    return per_field


def main() -> None:
    data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    records = data["records"]
    result = {
        "n_fixtures": len(records),
        "condition": "as_deployed",
        "source": str(IN_PATH.relative_to(ROOT).as_posix()),
        "hedge_fields": HEDGE_FIELDS,
        "per_field": analyze(records, "as_deployed"),
    }
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nWritten: {OUT_PATH}")


if __name__ == "__main__":
    main()
