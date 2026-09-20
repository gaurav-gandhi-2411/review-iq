"""Fail if any scheduled GitHub Actions workflow has no failure -> GitHub-issue alert path.

Why this exists (Session 15c): failover-probe.yml failed 7 nights running (2026-09-13..19)
and opened no issue, because it never used the shared alert action -- contradicting
ADR 0029's claim that every scheduled caller falls back to a GitHub issue. The alert-path
canary (.github/workflows/alert-path-canary.yml) only exercises the composite action
itself, so any workflow that simply did not call the action was invisible to it. This
script is the static counterpart: it reads every workflow file and requires that each one
with a `schedule` trigger contains a recognised alert path. SLACK_WEBHOOK_URL has never been
configured, so "no alert path" means a red scheduled run reaches nobody but GitHub's own
native email.

RECOGNISED alert shapes (any one is enough), matched on non-comment text of the file:
  1. A step whose `uses:` is exactly `./.github/actions/schedule-failure-alert`
     (the composite action; bare `uses:` key or `- uses:` list item, optionally quoted).
  2. Inline issue creation: the text `gh issue create` (a run step) or `issues.create`
     (actions/github-script, e.g. `github.rest.issues.create`).

NOT recognised (a workflow relying only on one of these FAILS -- fail closed):
  - Delegating alerting to a reusable workflow (`uses: ./.github/workflows/...`) or to a
    different local/remote action.
  - Slack / email / webhook notification of any kind (never configured in this repo).
  - Alerting spelled another way (`gh api .../issues -X POST`, `octokit.request(...)`).
  - Any mention of the above inside a YAML comment (comments are stripped first).

KNOWN LOOSENESS (fails open, by design of a text-level check): the check is per workflow
FILE, not per job or per step, and it does not evaluate `if:` conditions. A scheduled
workflow with two jobs where only one job alerts passes; a string literal that merely
contains `gh issue create` (e.g. in an `echo`) also passes. Reviewers must still read the
step's `if:` (see docs/architecture/adr/0029-alert-path-verification-and-canary.md).

Parsing: PyYAML is only a transitive dev dependency of this repo (via `datasets` /
`huggingface_hub`), not a declared one, so this script is stdlib-only and does a
structural text scan instead of a full YAML parse. "Cannot parse" -- fail closed -- means:
file is not UTF-8, is empty, has a tab in leading indentation (illegal in YAML), has no
top-level `on:` trigger key, or has no top-level `jobs:` key. Scheduled detection handles
`on:` as a block mapping with a `schedule:` child, and as an inline / flow value
(`on: schedule`, `on: [push, schedule]`, `on: {schedule: ...}`).

ALLOWLIST: a workflow may be exempted only with a per-entry reason string. It must stay
empty unless a workflow is genuinely exempt.

Usage:
    uv run python scripts/check_scheduled_workflows_alert.py [workflows_dir]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# workflow filename -> reason it is exempt. Must start (and, ideally, stay) empty.
ALLOWLIST: dict[str, str] = {}

_COMPOSITE_USES = re.compile(
    r"""^\s*(?:-\s+)?uses:\s*['"]?\./\.github/actions/schedule-failure-alert/?['"]?\s*$"""
)
_INLINE_ISSUE_CREATE = re.compile(r"\bgh\s+issue\s+create\b|\bissues\.create\b")
_ON_KEY = re.compile(r"""^(?:on|"on"|'on')\s*:(.*)$""")
_JOBS_KEY = re.compile(r"""^(?:jobs|"jobs"|'jobs')\s*:""")
_SCHEDULE_WORD = re.compile(r"\bschedule\b")


class WorkflowParseError(ValueError):
    """The workflow file could not be structurally understood (callers must fail closed)."""


def _strip_comment(line: str) -> str:
    """Drop a YAML/shell comment: `#` at line start or preceded by whitespace.

    Deliberately over-strips (e.g. a ` #` inside a quoted string) -- over-stripping can only
    hide an alert path (fail closed), never invent one out of a comment.
    """
    return re.sub(r"(^|\s)#.*$", "", line).rstrip()


def _clean_lines(text: str) -> list[str]:
    """Return non-blank, comment-stripped lines, raising on YAML-illegal tab indentation."""
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        if re.match(r"^ *\t", stripped):
            raise WorkflowParseError("tab character in indentation (illegal in YAML)")
        lines.append(stripped)
    return lines


def has_schedule_trigger(text: str) -> bool:
    """True if the workflow's top-level `on:` includes a `schedule` trigger.

    Raises WorkflowParseError if the structure cannot be understood.
    """
    lines = _clean_lines(text)
    if not lines:
        raise WorkflowParseError("file is empty")
    if not any(_JOBS_KEY.match(line) for line in lines):
        raise WorkflowParseError("no top-level `jobs:` key")

    on_index = next((i for i, line in enumerate(lines) if _ON_KEY.match(line)), None)
    if on_index is None:
        raise WorkflowParseError("no top-level `on:` trigger key")
    inline = _ON_KEY.match(lines[on_index]).group(1).strip()  # type: ignore[union-attr]

    # The `on:` block: every following line indented deeper than column 0.
    block: list[str] = []
    for line in lines[on_index + 1 :]:
        if not line.startswith((" ", "\t")):
            break
        block.append(line)

    if inline:
        # Inline scalar / flow value; a multi-line flow mapping continues in `block`.
        return bool(_SCHEDULE_WORD.search(inline + " " + " ".join(block)))
    if not block:
        raise WorkflowParseError("`on:` has no value and no children")
    child_indent = len(block[0]) - len(block[0].lstrip(" "))
    key = re.compile("^" + " " * child_indent + r"""(?:schedule|"schedule"|'schedule')\s*:""")
    return any(key.match(line) for line in block)


def has_alert_path(text: str) -> bool:
    """True if the (comment-stripped) workflow text contains a recognised alert shape."""
    for line in _clean_lines(text):
        if _COMPOSITE_USES.match(line) or _INLINE_ISSUE_CREATE.search(line):
            return True
    return False


def check_workflow(path: Path) -> str | None:
    """Return a failure message for one workflow file, or None if it is fine/not scheduled."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"{path.name}: cannot read workflow file ({exc}) -- failing closed"
    try:
        scheduled = has_schedule_trigger(text)
        if not scheduled:
            return None
        alerting = has_alert_path(text)
    except WorkflowParseError as exc:
        return f"{path.name}: cannot parse workflow ({exc}) -- failing closed"
    if alerting:
        return None
    if path.name in ALLOWLIST:
        return None
    return (
        f"{path.name}: has a `schedule` trigger but no failure -> GitHub-issue alert path. "
        "Add a final step using ./.github/actions/schedule-failure-alert "
        "(`if: always() && !cancelled() && github.event_name == 'schedule'`, needs "
        "`issues: write`)."
    )


def check_directory(workflows_dir: Path) -> list[str]:
    """Return failure messages for every workflow under `workflows_dir` (empty = all good)."""
    files = sorted([*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")])
    if not files:
        return [f"{workflows_dir}: no workflow files found -- failing closed"]
    failures = [msg for f in files if (msg := check_workflow(f)) is not None]
    for name, reason in ALLOWLIST.items():
        if not reason.strip():
            failures.append(f"ALLOWLIST entry {name!r} has no reason string")
    return failures


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code (0 = every scheduled workflow alerts)."""
    args = sys.argv[1:] if argv is None else argv
    workflows_dir = Path(args[0]) if args else DEFAULT_WORKFLOWS_DIR
    failures = check_directory(workflows_dir)
    if failures:
        for msg in failures:
            print(f"FAIL: {msg}")
        return 1
    print(f"OK: every scheduled workflow in {workflows_dir} has a failure-alert path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
