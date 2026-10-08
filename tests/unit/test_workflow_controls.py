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


def _executed_workflow_text(workflows_dir: Path) -> str:
    """Only what a workflow EXECUTES: every step's `run:` script minus shell comment lines.

    S19 Z8 induction: the previous version matched a script's name against the raw YAML text,
    so a new scripts/check_x.py mentioned only in a `#` comment of ci.yml counted as wired.
    """
    chunks: list[str] = []
    for path in sorted(workflows_dir.glob("*.yml")):
        for _job_id, _job, step in _steps(_load(path)):
            for line in str(step.get("run") or "").splitlines():
                if not line.lstrip().startswith("#"):
                    chunks.append(line)
    return "\n".join(chunks)


def test_executed_workflow_text_ignores_yaml_and_shell_comments(tmp_path: Path) -> None:
    (tmp_path / "wf.yml").write_text(
        "# scripts/check_in_yaml_comment.py\n"
        "on: push\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: |\n"
        "          # scripts/check_in_shell_comment.py\n"
        "          python scripts/check_really_run.py\n",
        encoding="utf-8",
    )
    text = _executed_workflow_text(tmp_path)
    assert "check_really_run.py" in text
    assert "check_in_yaml_comment.py" not in text
    assert "check_in_shell_comment.py" not in text


def test_every_check_script_is_wired_into_a_workflow_or_declared_manual() -> None:
    """A guard script no workflow runs guards nothing. scripts/check_*.py and probe_*.py must
    be EXECUTED by a workflow step (a comment does not count), or listed here with the reason
    it is a manual tool."""
    manual = {
        "check_site_responsive.py": "developer browser check, documented as not a CI gate",
    }
    workflow_text = _executed_workflow_text(WORKFLOWS_DIR)
    scripts = sorted(
        [*(REPO_ROOT / "scripts").glob("check_*.py"), *(REPO_ROOT / "scripts").glob("probe_*.py")]
    )
    assert len(scripts) >= 15
    unwired = [s.name for s in scripts if s.name not in workflow_text and s.name not in manual]
    assert not unwired, f"guard scripts no workflow runs: {unwired}"
    for name in manual:
        assert name not in workflow_text, f"{name} is now wired in; drop it from `manual`"


# Branch protection's required contexts -> the (workflow file, job id) that must report them.
# Read with `gh api repos/gaurav-gandhi-2411/review-iq/branches/main/protection` on 2026-10-07
# (S19 Z8); the earlier version of this test pinned only the first two of the five. A renamed job
# makes its required context never report, which blocks every merge until protection is edited.
REQUIRED_CONTEXTS: dict[str, tuple[str, str]] = {
    "lint-and-test": ("ci.yml", "lint-and-test"),
    "web-build": ("ci.yml", "web-build"),
    "manifest-provenance": ("ci.yml", "manifest-provenance"),
    "diff-scan": ("secret-scan.yml", "diff-scan"),
    "bypassrls-check": ("bypassrls-container-check.yml", "bypassrls-check"),
}


@pytest.mark.parametrize("context", sorted(REQUIRED_CONTEXTS))
def test_required_status_check_jobs_exist(context: str) -> None:
    workflow, job = REQUIRED_CONTEXTS[context]
    assert job in _load(WORKFLOWS_DIR / workflow)["jobs"]


# (workflow, test id or file) -> why a pytest step may skip it. A deselected test is a control that
# runs nowhere: S19 audit F9 found the forged-JWT tenant-isolation vector deselected in
# pre-cutover-verification.yml, so the isolation suite looked green without it. A new entry needs
# the reason the test CANNOT run in CI, not that it failed.
PYTEST_SKIP_ALLOWLIST: dict[tuple[str, str], str] = {
    ("ci.yml", "tests/integration"): (
        "needs a live Postgres; run by the pre-cutover-verification.yml job (ephemeral Postgres)"
    ),
    ("security-bypassrls-check.yml", "-k not ..."): (
        "TestCrossOrgSweepFunctionsSeeEveryOrg needs a superuser connection this job must not "
        "hold; the class runs in pre-cutover-verification.yml's public-service pass"
    ),
    ("pre-cutover-verification.yml", "tests/integration/test_resend_e2e.py"): (
        "sends real email through a real Resend key; CI has none"
    ),
    ("pre-cutover-verification.yml", "tests/integration/test_admin.py"): (
        "needs SERVICE_ROLE=admin; run in the separate admin-service step of the same job"
    ),
    ("pre-cutover-verification.yml", "tests/integration/test_account_deletion.py"): (
        "needs SERVICE_ROLE=admin; run in the separate admin-service step of the same job"
    ),
}


def _pytest_skips(run: str) -> list[str]:
    """Every --deselect / --ignore / `-k "not ..."` target in a pytest command (shell
    line continuations joined). A --deselect target is reported as the full test id; an
    --ignore target as the path."""
    skips: list[str] = []
    for line in run.replace("\\\n", " ").splitlines():
        if "pytest" not in line:
            continue
        skips += [
            m.group(1)
            for m in re.finditer(r"""--(?:deselect|ignore(?:-glob)?)[ =]["']?([^\s"']+)""", line)
        ]
        if re.search(r"""-k\s+["']?\s*not\b""", line):
            skips.append("-k not ...")
    return skips


def test_pytest_steps_do_not_deselect_tests_without_an_allowlisted_reason() -> None:
    for name, wf in _all_workflows().items():
        for _job_id, _job, step in _steps(wf):
            for target in _pytest_skips(step.get("run") or ""):
                assert (name, target) in PYTEST_SKIP_ALLOWLIST, (
                    f"{name} step {step.get('name')!r} skips {target!r} "
                    "(--deselect / --ignore / -k 'not ...'): a skipped test is a control that "
                    "runs nowhere (S19 audit F9). Fix the test or add an allowlisted reason."
                )


def test_pytest_skip_allowlist_has_no_stale_entries_and_admin_files_run() -> None:
    """Every allowlist entry must still be skipped by its workflow, and the two files skipped
    from the public pass only because they need SERVICE_ROLE=admin must run in the admin step."""
    seen: set[tuple[str, str]] = set()
    admin_run = ""
    for name, wf in _all_workflows().items():
        for _job_id, _job, step in _steps(wf):
            run = step.get("run") or ""
            seen |= {(name, t) for t in _pytest_skips(run)}
            if (
                name == "pre-cutover-verification.yml"
                and (step.get("env") or {}).get("SERVICE_ROLE") == "admin"
            ):
                admin_run += run
    assert seen == set(PYTEST_SKIP_ALLOWLIST), seen ^ set(PYTEST_SKIP_ALLOWLIST)
    assert "tests/integration/test_admin.py" in admin_run
    assert "tests/integration/test_account_deletion.py" in admin_run


def test_pytest_skip_detector_catches_each_shape() -> None:
    cmd = (
        "uv run pytest tests/ -v \\\n"
        '  --deselect "tests/a.py::T::t" \\\n'
        "  --ignore=tests/b.py \\\n"
        "  -k 'not slow'"
    )
    assert _pytest_skips(cmd) == ["tests/a.py::T::t", "tests/b.py", "-k not ..."]
    assert _pytest_skips("uv run pytest tests/ -m integration --no-cov") == []
