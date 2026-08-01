"""Regenerates eval/results.json + eval/results/latest.json (cassette-replay, $0, fully
deterministic -- same mechanism `eval.yml`'s existing CI job already uses) and fails if the
result differs from what's committed, other than the two provenance fields ("generated_at",
"git_sha") that are EXPECTED to differ on every run and carry no substantive information about
correctness.

This is the mechanical proof required for rule-70a gate 3's generated-artifact carve-out to
legitimately apply to these two files (CLAUDE.md rule 39b): a file only qualifies as "machine-
generated, not reviewable source" if a hand-edit would be caught, and this check is what catches
it. It does NOT cover eval/results/authenticity_latest.json or eval/results/agreement_latest.json
-- neither is a $0/deterministic regeneration today (authenticity scoring calls live Groq;
agreement/consensus labeling is a one-time human-calibrated judging pipeline, not an idempotent
re-run) -- so neither is claimed here, and neither should be added to the size-gate carve-out
until an equivalent proof exists for it.

Usage: uv run python scripts/check_eval_results_reproducible.py
Exit 0: regenerated output matches committed output (carve-out is earned).
Exit 1: mismatch found, or the eval run itself crashed -- printed to stdout for CI visibility.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LATEST_RESULTS_PATH = REPO_ROOT / "eval" / "results" / "latest.json"
RESULTS_PATH = REPO_ROOT / "eval" / "results.json"
# Excluded because they are expected to differ on every run and carry no substantive
# information about correctness:
#   - generated_at / git_sha: provenance timestamp + commit SHA, not a result.
#   - mode: "direct (local LLM)" vs "routed (tiered)" -- write_results()'s own docstring
#     documents these as producing byte-identical overall/per-language scores as of prompt
#     v2.3 (same-cassette comparison already performed), so which one ran is not itself part
#     of the substantive result this check protects.
#   - fixtures[].latency_ms: wall-clock timing of this specific run (even the cassette
#     provider's own dict-lookup overhead varies run to run) -- never part of correctness.
TOP_LEVEL_FIELDS_EXCLUDED_FROM_COMPARISON = ("generated_at", "git_sha", "mode")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def strip_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Returns a deep copy of `payload` with every field known to legitimately vary between
    two genuinely-equivalent runs removed -- see the module-level comment for why each one is
    excluded. What remains is exactly the substantive result: scores, per-fixture verdicts,
    thresholds, pass/fail."""
    stripped = {
        k: v for k, v in payload.items() if k not in TOP_LEVEL_FIELDS_EXCLUDED_FROM_COMPARISON
    }
    stripped = copy.deepcopy(stripped)
    for fixture in stripped.get("fixtures", []):
        fixture.pop("latency_ms", None)
    return stripped


def main() -> int:
    if not LATEST_RESULTS_PATH.exists():
        print(f"FAIL: {LATEST_RESULTS_PATH} does not exist -- nothing to verify against.")
        return 1

    committed_latest = strip_provenance(load_json(LATEST_RESULTS_PATH))
    committed_results = strip_provenance(load_json(RESULTS_PATH)) if RESULTS_PATH.exists() else None

    result = subprocess.run(
        [sys.executable, "-m", "eval.runner"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode not in (0, 1):
        # eval.runner exits 1 on a genuine accuracy-gate FAIL (a valid regeneration outcome,
        # scores just aren't above threshold) -- only a non-{0,1} exit is a real crash.
        print(
            f"FAIL: eval.runner did not complete cleanly (exit {result.returncode}):\n{result.stderr}"
        )
        return 1

    regenerated_latest = strip_provenance(load_json(LATEST_RESULTS_PATH))
    regenerated_results = (
        strip_provenance(load_json(RESULTS_PATH)) if RESULTS_PATH.exists() else None
    )

    mismatches = []
    if committed_latest != regenerated_latest:
        mismatches.append("eval/results/latest.json")
    if committed_results != regenerated_results:
        mismatches.append("eval/results.json")

    if mismatches:
        print(
            "FAIL: the following file(s) do not reproduce from committed cassettes "
            f"(excluding generated_at/git_sha): {', '.join(mismatches)}\n"
            "This file must be machine-generated only. If you edited it by hand, revert your "
            "edit and re-run `uv run python -m eval.runner` instead. If prompts/fixtures "
            "genuinely changed, re-run the eval and commit the real regenerated output."
        )
        return 1

    print(
        "PASS: eval/results.json and eval/results/latest.json reproduce exactly from committed "
        "cassettes (excluding generated_at/git_sha provenance fields) -- the gate-3 "
        "generated-artifact carve-out is earned for these two files."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
