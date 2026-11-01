"""Behavioural test for db-backup.yml's "Verify dump integrity" step (S19 Z8).

The structural test in test_workflow_controls.py only checks that the string `COPY public` is in
the step. That string is NOT evidence of data: pg_dump writes a `COPY public.<table> (...) FROM
stdin;` block for every table, including tables with zero rows, so an all-empty data dump
passed the step. This test executes the step's real `run:` script (extracted from the YAML,
not re-typed here) against synthetic dumps shaped like pg_dump's text output.

SURFACE: needs bash, gzip, gunzip, awk and grep on PATH (ubuntu-latest has them). It proves the
step's decision logic on dump text; it does not prove pg_dump produced that text (the induction
log in docs/control-audit-s19.md shows real pg_dump 17 output for the same three shapes).
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "db-backup.yml"

_HEADER = (
    "CREATE TABLE public.organizations (id uuid);\n"
    "CREATE TABLE public.authenticity_audits (id uuid);\n"
)


def _verify_script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job["steps"]:
            if step.get("name") == "Verify dump integrity":
                return str(step["run"])
    raise AssertionError("db-backup.yml has no 'Verify dump integrity' step")


def _run_step(tmp_path: Path, dump_text: str | bytes) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash, "bash is required to execute the workflow step"
    dump = tmp_path / "dump.sql.gz"
    if isinstance(dump_text, bytes):
        dump.write_bytes(dump_text)
    else:
        dump.write_bytes(gzip.compress(dump_text.encode("utf-8")))
    script = tmp_path / "step.sh"
    script.write_text(_verify_script(), encoding="utf-8", newline="\n")
    return subprocess.run(  # noqa: S603 -- executes this repo's own workflow step on test data
        [bash, "-e", script.as_posix()],
        env={**os.environ, "FILENAME": dump.as_posix()},
        capture_output=True,
        text=True,
        check=False,
    )


def test_dump_with_organization_rows_passes(tmp_path: Path) -> None:
    text = _HEADER + "COPY public.organizations (id) FROM stdin;\n11111111-1111\n\\.\n"
    result = _run_step(tmp_path, text)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public.organizations has 1 row(s)" in result.stdout


def test_all_empty_data_dump_is_rejected(tmp_path: Path) -> None:
    """The decorative case: every token the old step looked for is present, zero rows."""
    text = (
        _HEADER
        + "COPY public.organizations (id) FROM stdin;\n\\.\n"
        + "COPY public.authenticity_audits (id) FROM stdin;\n\\.\n"
    )
    result = _run_step(tmp_path, text)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "0 rows" in result.stdout


def test_rows_in_another_table_do_not_satisfy_the_organizations_check(tmp_path: Path) -> None:
    text = (
        _HEADER
        + "COPY public.organizations (id) FROM stdin;\n\\.\n"
        + "COPY public.authenticity_audits (id) FROM stdin;\nabc\n\\.\n"
    )
    result = _run_step(tmp_path, text)
    assert result.returncode == 1, result.stdout + result.stderr


def test_schema_only_dump_is_rejected(tmp_path: Path) -> None:
    result = _run_step(tmp_path, _HEADER)
    assert result.returncode == 1
    assert "COPY public" in result.stdout


@pytest.mark.parametrize("payload", [b"", b"not gzip at all"])
def test_empty_or_corrupt_file_is_rejected(tmp_path: Path, payload: bytes) -> None:
    result = _run_step(tmp_path, payload)
    assert result.returncode == 1
