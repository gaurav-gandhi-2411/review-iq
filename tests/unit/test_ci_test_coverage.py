"""Guard: every test file in the repo is collected by a CI pytest command, or is on a list.

Why: S19 control audit F8. tests/benchmark/ (111 tests: corpus licence provenance, PII-scrub
wiring, held-out leakage, the published benchmark scorers) was excluded by `--ignore` in both
ci.yml and pyproject addopts and never ran; benchmark/**/test_*.py lived beside their code and
no command collected them. Both were green-by-absence. This test makes the exclusion
unable to grow silently: a new test file that no CI command reaches fails here unless its
directory is added to EXCLUDED_FROM_CI with a reason.

SURFACE: reads ci.yml `pytest` invocations and pyproject addopts `--ignore`s textually. It
proves a file is reachable by a CI command's path arguments, not that the step is gated
by a condition (a step with `if:` that never fires would pass this).
"""

from __future__ import annotations

import os
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "web", "__pycache__", ".claude"}

# Directory prefix -> why no `ci.yml` pytest command collects it, and where it runs instead.
EXCLUDED_FROM_CI: dict[str, str] = {
    "tests/integration": (
        "Needs a live Postgres (-m integration). Run by pre-cutover-verification.yml "
        "(ephemeral PG 17) and bypassrls-container-check.yml, not by lint-and-test."
    ),
}


def _test_files() -> list[str]:
    out: list[str] = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".venv")]
        for f in files:
            # test_*.py only (house rule: never *_test.py); benchmark/phase2_synthetic/
            # generate_stress_test.py is a data generator that merely matches `*_test.py`.
            if f.endswith(".py") and f.startswith("test_"):
                out.append((Path(root) / f).relative_to(REPO_ROOT).as_posix())
    return sorted(out)


def _ci_pytest_commands() -> list[tuple[list[str], list[str]]]:
    """(paths, ignores) for each `pytest` invocation in ci.yml run steps."""
    wf: dict[str, Any] = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    cmds: list[tuple[list[str], list[str]]] = []
    for job in wf["jobs"].values():
        for step in job.get("steps") or []:
            for line in (step.get("run") or "").splitlines():
                if not re.search(r"\bpytest\b", line):
                    continue
                toks = shlex.split(line.split("pytest", 1)[1])
                paths = [t for t in toks if not t.startswith("-")]
                ignores = [t.split("=", 1)[1] for t in toks if t.startswith("--ignore=")]
                cmds.append((paths, ignores))
    return cmds


def _addopts_ignores() -> list[str]:
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = cfg["tool"]["pytest"]["ini_options"].get("addopts", "")
    return [t.split("=", 1)[1] for t in shlex.split(addopts) if t.startswith("--ignore=")]


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def _covering(path: str) -> list[str]:
    hits = []
    for paths, ignores in _ci_pytest_commands():
        if any(_under(path, p) for p in paths) and not any(
            _under(path, i) for i in ignores + _addopts_ignores()
        ):
            hits.append(" ".join(paths))
    return hits


def test_every_test_file_is_collected_by_ci_or_explicitly_excluded() -> None:
    files = _test_files()
    # An empty/mis-rooted walk would make every assertion below pass vacuously.
    assert len(files) >= 100, f"only {len(files)} test files found; the walk is mis-rooted"
    uncovered = [
        f for f in files if not _covering(f) and not any(_under(f, x) for x in EXCLUDED_FROM_CI)
    ]
    assert not uncovered, (
        "test files that no ci.yml pytest command collects and that are not in "
        f"EXCLUDED_FROM_CI (add a CI step, or an excluded dir with a reason): {uncovered}"
    )


def test_excluded_dirs_are_real_and_really_excluded() -> None:
    files = _test_files()
    for prefix in EXCLUDED_FROM_CI:
        members = [f for f in files if _under(f, prefix)]
        assert members, f"stale EXCLUDED_FROM_CI entry (no tests there any more): {prefix}"
        collected = [f for f in members if _covering(f)]
        assert not collected, f"{prefix} is listed as excluded but CI collects {collected[:3]}"


def test_benchmark_tests_are_not_ignored_anywhere() -> None:
    # The specific regression: tests/benchmark must not be re-ignored in either place.
    assert not any(_under("tests/benchmark/x.py", i) for i in _addopts_ignores())
    assert any(_covering(f) for f in _test_files() if f.startswith("tests/benchmark/"))


def test_beside_code_test_dirs_each_get_their_own_invocation() -> None:
    # They use bare sibling imports and share the module name `common`; one combined run
    # would clash. Require the covering invocation's path to be exactly the test's directory.
    beside = [f for f in _test_files() if not f.startswith("tests/")]
    assert len(beside) >= 4, f"expected the benchmark/ beside-code tests, found {beside}"
    for f in beside:
        parent = f.rsplit("/", 1)[0]
        assert parent in _covering(f), f"{f} must be collected by `pytest {parent}` on its own"
