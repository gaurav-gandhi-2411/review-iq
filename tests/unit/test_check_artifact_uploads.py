from __future__ import annotations

from typing import Any

from scripts import check_artifact_uploads as g


def _wf(path: str, guard: bool = True) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if guard:
        steps.append({"name": "Assert the upload set is encrypted-only (structural guard)"})
    steps.append({"uses": "actions/upload-artifact@v4", "with": {"path": path}})
    return {"jobs": {"j": {"steps": steps}}}


def test_current_repo_passes() -> None:
    assert g.main() == 0


def test_db_backup_accepts_only_the_literal_glob_with_guard() -> None:
    assert g.check_workflow("db-backup.yml", _wf("upload/*.gpg")) == []
    assert g.check_workflow("db-backup.yml", _wf("${{ steps.x.outputs.f }}"))
    assert g.check_workflow("db-backup.yml", _wf("review-iq-db-1.sql.gz"))
    assert g.check_workflow("db-backup.yml", _wf("upload/*.gpg", guard=False))


def test_any_workflow_flags_plaintext_dump_paths_but_not_gpg_or_unrelated() -> None:
    assert g.check_workflow("x.yml", _wf("out/prod_dump.sql.gz", guard=False))
    assert g.check_workflow("x.yml", _wf("out/backup.tar", guard=False))
    assert g.check_workflow("x.yml", _wf("out/backup.sql.gz.gpg", guard=False)) == []
    assert g.check_workflow("x.yml", _wf("eval/report.md\neval/results.json", guard=False)) == []
