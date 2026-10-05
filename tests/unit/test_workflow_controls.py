"""Structural tests that keep the CI/monitoring workflows from going decorative.

Why: five controls in this repo reported success without verifying their claim (see
docs/decorative-control-sweep.md). Several of those shapes live in workflow YAML, which no
unit test read: a step that swallows a failure, an alert step that only runs on success, a
handler keyed on exit codes that misses some, a verification that checks less than its name.
These tests parse every workflow with PyYAML and pin the shapes a reviewer must otherwise
spot by eye. They are hermetic (no network, no shell).

SURFACE: only what the YAML says. They cannot prove a step's shell logic works at runtime --
that was exercised by hand in the sweep (docs/decorative-control-sweep.md, evidence column).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
ALERT_ACTION = "./.github/actions/schedule-failure-alert"

# (workflow file, job id, step name) -> why a failure of this step may be swallowed.
# Anything else with `continue-on-error: true` is a step whose failure can never turn the
# job red -- the exact shape of a decorative control.
CONTINUE_ON_ERROR_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("ci.yml", "pip-audit", "pip-audit (non-blocking)"): (
        "Declared informational-only in ci.yml's own comment (T1 audit policy); npm-audit is "
        "the blocking dependency gate. Nothing consumes its result."
    ),
    ("alert-path-canary.yml", "canary", "Deliberately fail (this is the point, not a bug)"): (
        "The canary's deliberate failure; the verify step after it is the real check."
    ),
}


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} did not parse to a mapping"
    return data


def _all_workflows() -> dict[str, dict[str, Any]]:
    files = sorted(WORKFLOWS_DIR.glob("*.yml"))
    # An empty glob would make every parametrised test below pass vacuously.
    assert len(files) >= 15, f"expected the repo's workflows, found {len(files)}"
    return {f.name: _load(f) for f in files}


def _steps(wf: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    out = []
    for job_id, job in (wf.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            out.append((job_id, job, step))
    return out


def _triggers(wf: dict[str, Any]) -> Any:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return wf.get("on", wf.get(True))


def test_all_workflows_parse_and_have_jobs() -> None:
    for name, wf in _all_workflows().items():
        assert wf.get("jobs"), f"{name} has no jobs"
        assert _triggers(wf), f"{name} has no triggers"


def test_continue_on_error_only_where_allowlisted() -> None:
    found: set[tuple[str, str, str]] = set()
    for name, wf in _all_workflows().items():
        for job_id, _job, step in _steps(wf):
            if step.get("continue-on-error") is True:
                found.add((name, job_id, step.get("name", "<unnamed>")))
    unexpected = found - set(CONTINUE_ON_ERROR_ALLOWLIST)
    assert not unexpected, (
        f"steps whose failure is swallowed, not allowlisted: {sorted(unexpected)}"
    )
    stale = set(CONTINUE_ON_ERROR_ALLOWLIST) - found
    assert not stale, f"stale allowlist entries (step renamed or fixed): {sorted(stale)}"


def test_no_failure_swallowing_shell_idioms() -> None:
    # `|| true` / `--exit-zero` turn a failing command into success; `set +e` is allowed only
    # in demo-quota-probe.yml, whose exit-code handlers are pinned by its own test below.
    bad: list[str] = []
    for name, wf in _all_workflows().items():
        for job_id, _job, step in _steps(wf):
            run = step.get("run") or ""
            label = f"{name}:{job_id}:{step.get('name', '<unnamed>')}"
            if re.search(r"\|\|\s*true\b|--exit-zero|\|\|\s*exit\s+0\b", run):
                bad.append(f"{label} swallows a failure")
            if "set +e" in run and name != "demo-quota-probe.yml":
                bad.append(f"{label} disables errexit")
    assert not bad, bad


def _status_is_computed(step: dict[str, Any]) -> bool:
    status = str((step.get("with") or {}).get("status", ""))
    return "${{" in status and any(k in status for k in ("job.status", ".outcome", ".result"))


def test_alert_step_runs_after_a_failed_step() -> None:
    """A computed-status alert step without `always()` never runs on the failure it exists for.

    GitHub skips every later step once one fails unless the step's `if` says otherwise, so an
    alert step gated only by success can only ever send the *recovery* message.
    """
    checked = 0
    for name, wf in _all_workflows().items():
        for job_id, job, step in _steps(wf):
            if step.get("uses") != ALERT_ACTION or not _status_is_computed(step):
                continue
            checked += 1
            cond = f"{step.get('if', '')} {job.get('if', '')}"
            assert "always()" in cond, (
                f"{name}:{job_id}:{step.get('name')} computes its status from an earlier step "
                "but is not gated by always() -- it would be skipped when that step fails"
            )
    assert checked >= 8, f"only {checked} computed-status alert steps found; scan went stale"


def test_scheduled_alert_steps_are_scoped_or_deliberate() -> None:
    # Literal status: "failure" callers (canary, demo-quota-probe) must be gated by their own
    # condition or by a preceding continue-on-error step, never run unconditionally.
    for name, wf in _all_workflows().items():
        for job_id, _job, step in _steps(wf):
            if step.get("uses") != ALERT_ACTION:
                continue
            status = str((step.get("with") or {}).get("status", ""))
            if status.strip("'\"") == "failure":
                assert name in {"alert-path-canary.yml", "demo-quota-probe.yml"}, (
                    f"{name}:{job_id} sends a literal failure alert unconditionally"
                )


def test_demo_quota_probe_fails_on_unexpected_exit_codes() -> None:
    """The probe step runs under `set +e` and only publishes its exit code, so a handler must
    exist for every code or an unlisted one (137, 127, ...) leaves the job green."""
    wf = _load(WORKFLOWS_DIR / "demo-quota-probe.yml")
    steps = [s for _j, _job, s in _steps(wf)]
    probe = next(s for s in steps if s.get("id") == "probe")
    # Exit 2 must only count as a 429 when the probe printed its own 429 line, because uv and
    # argparse also exit 2 on their own errors.
    assert "PIPESTATUS" in probe["run"] and "429" in probe["run"]
    catch_all = [s for s in steps if "unexpected probe exit code" in s.get("name", "")]
    assert len(catch_all) == 1
    cond = catch_all[0]["if"]
    for handled in ("'0'", "'1'", "'2'"):
        assert f"exit_code != {handled}" in cond
    assert "exit 1" in catch_all[0]["run"]


def test_db_backup_verifies_data_not_just_schema() -> None:
    """A schema-only dump contains CREATE TABLE plus the table names, so the old token checks
    passed a backup with no rows in it. Require pg_dump's data section marker as well."""
    wf = _load(WORKFLOWS_DIR / "db-backup.yml")
    verify = next(s for _j, _job, s in _steps(wf) if s.get("name") == "Verify dump integrity")
    assert 'check_token "COPY public' in verify["run"]


def test_gitleaksignore_does_not_grow_silently() -> None:
    """Every fingerprint is a finding waved through. Raising this bound must be a reviewed
    decision (the entry needs a checkable reason), not a side effect of silencing a scan."""
    lines = [
        ln.strip()
        for ln in (REPO_ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    fingerprint = re.compile(r"^[0-9a-f]{40}:[^:\s]+:[a-z0-9-]+:\d+$")
    assert all(fingerprint.match(ln) for ln in lines), "malformed .gitleaksignore fingerprint"
    assert len(lines) <= 20, f"{len(lines)} fingerprints; was 20 when this bound was set"


def test_every_check_script_is_wired_into_a_workflow_or_declared_manual() -> None:
    """A guard script no workflow runs guards nothing. scripts/check_*.py and probe_*.py must
    be referenced by a workflow, or listed here with the reason it is a manual tool."""
    manual = {
        "check_site_responsive.py": "developer browser check, documented as not a CI gate",
    }
    workflow_text = "\n".join(f.read_text(encoding="utf-8") for f in WORKFLOWS_DIR.glob("*.yml"))
    scripts = sorted(
        [*(REPO_ROOT / "scripts").glob("check_*.py"), *(REPO_ROOT / "scripts").glob("probe_*.py")]
    )
    assert len(scripts) >= 15
    unwired = [s.name for s in scripts if s.name not in workflow_text and s.name not in manual]
    assert not unwired, f"guard scripts no workflow runs: {unwired}"
    for name in manual:
        assert name not in workflow_text, f"{name} is now wired in; drop it from `manual`"


@pytest.mark.parametrize("job", ["lint-and-test", "web-build"])
def test_required_status_check_jobs_exist_in_ci_yml(job: str) -> None:
    # Branch protection requires exactly these two contexts (verified with the GitHub API in
    # docs/decorative-control-sweep.md). If a job is renamed the required check never reports.
    assert job in _load(WORKFLOWS_DIR / "ci.yml")["jobs"]
