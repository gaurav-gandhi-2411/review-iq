"""Regenerates eval/results/known_gaps_n106.json (eval/analyze_known_gaps.py, $0, fully
deterministic -- reuses ground truth and as_deployed predictions already committed under
eval/fixtures/_held_out_hindi_hinglish/ and eval/results/held_out_scoring_v2.json, no live LLM
calls) and fails if the result differs from what's committed.

This is the mechanical proof required for rule-70a gate 3's generated-artifact carve-out to
legitimately apply to this file (Session 14 P2, PR #188): a file only qualifies as "machine-
generated, not reviewable source" if a hand-edit would be caught, and this check is what catches
it. Unlike eval/results.json/latest.json's check_eval_results_reproducible.py, this script writes
no provenance fields (no generated_at/git_sha) -- the output is compared byte-for-byte, no
exclusion needed.

Usage: uv run python scripts/check_known_gaps_reproducible.py
Exit 0: regenerated output matches committed output (carve-out is earned).
Exit 1: mismatch found, or the regeneration itself crashed -- printed to stdout for CI visibility.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "eval" / "results" / "known_gaps_n106.json"


def main() -> int:
    if not OUTPUT_PATH.exists():
        print(f"FAIL: {OUTPUT_PATH} does not exist -- nothing to verify against.")
        return 1

    committed = OUTPUT_PATH.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "eval.analyze_known_gaps"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"FAIL: eval/analyze_known_gaps.py did not complete cleanly:\n{result.stderr}")
        return 1

    regenerated = OUTPUT_PATH.read_text(encoding="utf-8")

    if committed != regenerated:
        print(
            "FAIL: eval/results/known_gaps_n106.json does not reproduce from committed fixtures "
            "and held_out_scoring_v2.json. This file must be machine-generated only. If you "
            "edited it by hand, revert your edit and re-run `uv run python "
            "eval/analyze_known_gaps.py` instead."
        )
        return 1

    print(
        "PASS: eval/results/known_gaps_n106.json reproduces exactly from committed fixtures -- "
        "the gate-3 generated-artifact carve-out is earned for this file."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
