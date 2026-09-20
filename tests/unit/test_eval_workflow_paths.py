"""Guards .github/workflows/eval.yml: the pull_request `paths:` filter must not drift from the push
one, and the PR path must not be able to open/close the repo-wide alert issue.

Why: eval.yml's push filter went stale once (c492e4a changed app/core/config.py, invalidated every
cassette, and the workflow never ran). Since U7b the same replay also runs on PRs from a second,
hand-maintained copy of that list -- two copies is exactly how a list goes stale, so this fails if
they differ. Text-level parse: PyYAML is not a declared dependency of this repo.
"""

from __future__ import annotations

import re
from pathlib import Path

EVAL_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "eval.yml"


def _paths_for(trigger: str, text: str) -> list[str]:
    """Return the `paths:` entries under `on: <trigger>:` (comments and quotes stripped)."""
    lines = text.splitlines()
    in_trigger = in_paths = False
    out: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^  " + re.escape(trigger) + r":\s*$", line):
            in_trigger, in_paths = True, False
            continue
        if in_trigger and re.match(r"^  \S", line):  # next sibling trigger
            break
        if in_trigger and re.match(r"^    paths:\s*$", line):
            in_paths = True
            continue
        if in_trigger and in_paths:
            m = re.match(r"^      - (.+?)\s*$", line)
            if m:
                out.append(m.group(1).strip("\"'"))
            elif line.strip() and not line.strip().startswith("#"):
                in_paths = False
    return out


def test_pull_request_paths_equal_push_paths() -> None:
    text = EVAL_YML.read_text(encoding="utf-8")
    push, pr = _paths_for("push", text), _paths_for("pull_request", text)
    assert push, "could not parse the push paths list -- update this test's parser"
    assert sorted(push) == sorted(pr), f"push {push} != pull_request {pr}"


def test_paths_include_the_files_that_move_the_eval() -> None:
    pr = _paths_for("pull_request", EVAL_YML.read_text(encoding="utf-8"))
    for required in ("app/core/**", "eval/fixtures/**", "eval/runner.py", "eval/cassettes/**"):
        assert required in pr, f"{required} missing from the pull_request paths filter"


def test_alert_step_is_skipped_on_pull_request() -> None:
    text = EVAL_YML.read_text(encoding="utf-8")
    m = re.search(r"- name: Alert on failure / close alert on recovery\n\s+if: (.+)\n", text)
    assert m, "alert step not found"
    assert "github.event_name != 'pull_request'" in m.group(1)


def test_no_continue_on_error_in_eval_workflow() -> None:
    # The workflow's own contract (see memory feedback_eval_ci): no escape hatch that lets a red
    # eval merge.
    text = EVAL_YML.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "continue-on-error" not in code
