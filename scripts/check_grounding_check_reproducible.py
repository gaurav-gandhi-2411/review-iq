"""Regenerates eval/results/grounding_check_fpr_n106.json (eval/measure_grounding_check.py, $0,
fully deterministic -- reuses as_deployed predictions and review texts already committed under
eval/results/held_out_scoring_v2.json and eval/fixtures/_held_out_hindi_hinglish/, no live LLM
calls) and fails if the result differs from what's committed.

This is the mechanical proof required for rule-70a gate 3's generated-artifact carve-out to
legitimately apply to this file (Session 14 P4c, PR #189): a file only qualifies as "machine-
generated, not reviewable source" if a hand-edit would be caught, and this check is what catches
it. No provenance fields are written -- the output is compared byte-for-byte, no exclusion needed.

Usage: uv run python scripts/check_grounding_check_reproducible.py
Exit 0: regenerated output matches committed output (carve-out is earned).
Exit 1: mismatch found, or the regeneration itself crashed -- printed to stdout for CI visibility.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "eval" / "results" / "grounding_check_fpr_n106.json"


def main() -> int:
    if not OUTPUT_PATH.exists():
        print(f"FAIL: {OUTPUT_PATH} does not exist -- nothing to verify against.")
        return 1

    committed = OUTPUT_PATH.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "eval.measure_grounding_check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"FAIL: eval/measure_grounding_check.py did not complete cleanly:\n{result.stderr}")
        return 1

    regenerated = OUTPUT_PATH.read_text(encoding="utf-8")

    if committed != regenerated:
        print(
            "FAIL: eval/results/grounding_check_fpr_n106.json does not reproduce from committed "
            "held_out_scoring_v2.json and fixtures. This file must be machine-generated only. If "
            "you edited it by hand, revert your edit and re-run `uv run python "
            "eval/measure_grounding_check.py` instead."
        )
        return 1

    print(
        "PASS: eval/results/grounding_check_fpr_n106.json reproduces exactly from committed "
        "fixtures -- the gate-3 generated-artifact carve-out is earned for this file."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
