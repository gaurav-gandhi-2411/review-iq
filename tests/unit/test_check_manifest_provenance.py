"""Failure-mode tests for scripts/check_manifest_provenance.py (hermetic: no git, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import scripts.check_manifest_provenance as mod


@pytest.fixture
def manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "metrics.json"
    monkeypatch.setattr(mod, "MANIFEST_PATH", path)
    monkeypatch.setattr(mod, "tracked_files", lambda: {"eval/results/latest.json"})

    def write(metrics: list[dict]) -> None:
        path.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")

    return write


def test_tracked_source_file_passes(manifest) -> None:
    manifest([{"id": "m", "source_file": "eval/results/latest.json"}])
    assert mod.main() == 0


def test_untracked_source_file_fails(manifest) -> None:
    manifest([{"id": "m", "source_file": "eval/results/gitignored.json"}])
    assert mod.main() == 1


def test_missing_source_file_field_fails(manifest) -> None:
    manifest([{"id": "m"}])
    assert mod.main() == 1


def test_KNOWN_GAP_free_text_citation_with_a_space_passes(manifest, capsys) -> None:
    """Pins the escape hatch: any source_file containing a space or '(' is treated as a
    'non-file citation' and only warned about, so a metric can cite prose and stay green.
    Nothing here checks the metric VALUE against the artifact -- render_metrics.py --check
    regenerates .portfolio/metrics.json whole-file, which is what actually protects the value."""
    manifest([{"id": "m", "source_file": "trust me (measured live)"}])
    assert mod.main() == 0
    assert "non-file citation" in capsys.readouterr().out


def test_KNOWN_GAP_absent_manifest_is_a_pass(tmp_path, monkeypatch, capsys) -> None:
    """Pins 'no manifest -> nothing to check -> exit 0'. Fine while .portfolio/metrics.json is
    committed, but a deleted or renamed manifest turns the CI job into a no-op."""
    monkeypatch.setattr(mod, "MANIFEST_PATH", tmp_path / "absent.json")
    assert mod.main() == 0
    assert "nothing to check" in capsys.readouterr().out


def test_real_manifest_is_present_so_the_ci_job_is_not_a_no_op() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = repo_root / ".portfolio" / "metrics.json"
    assert manifest_path.is_file()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["metrics"]
