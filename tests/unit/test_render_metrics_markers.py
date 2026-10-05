"""Unit tests for scripts/render_metrics.py marker verification (fail-closed --check).

Kept in its own file, not appended to test_render_metrics.py, so this PR and any other PR that
adds renderer tests never collide on the end of that file.
"""

from __future__ import annotations

from pathlib import Path

from scripts.render_metrics import UNRENDERED_BLOCK_ALLOWLIST, marker_problems


class TestMarkerProblems:
    """--check used to report OK for markers it silently never verified (rule 85a sweep)."""

    def test_known_block_is_clean(self):
        text = "<!-- METRICS:START:gate_summary -->x<!-- METRICS:END -->"
        assert marker_problems("doc.md", text) == []

    def test_typod_block_name_is_a_problem(self):
        text = "<!-- METRICS:START:gate_sumary -->78.6% accuracy<!-- METRICS:END -->"
        problems = marker_problems("doc.md", text)
        assert len(problems) == 1
        assert "gate_sumary" in problems[0]

    def test_unnamed_marker_is_a_problem(self):
        # check_no_hardcoded_metrics.py exempts an unnamed START..END span too.
        text = "<!-- METRICS:START -->78.6% accuracy<!-- METRICS:END -->"
        assert any("marker(s)" in p for p in marker_problems("doc.md", text))

    def test_start_without_end_is_a_problem(self):
        text = "<!-- METRICS:START:gate_summary -->78.6% accuracy, no end marker"
        assert any("marker(s)" in p for p in marker_problems("doc.md", text))

    def test_allowlisted_unrendered_block_is_accepted(self, monkeypatch):
        monkeypatch.setitem(UNRENDERED_BLOCK_ALLOWLIST, ("doc.md", "hand_typed"), "reason")
        text = "<!-- METRICS:START:hand_typed -->x<!-- METRICS:END -->"
        assert marker_problems("doc.md", text) == []

    def test_stale_allowlist_entry_is_a_problem(self, monkeypatch):
        monkeypatch.setitem(UNRENDERED_BLOCK_ALLOWLIST, ("doc.md", "gone"), "reason")
        assert any("stale" in p for p in marker_problems("doc.md", "no markers"))

    def test_real_target_files_have_no_marker_problems(self):
        # Ties the allowlist to reality: the committed README/site/SECURITY files pass.
        from scripts.render_metrics import REPO_ROOT, TARGET_FILES

        for path in TARGET_FILES:
            rel = path.relative_to(REPO_ROOT).as_posix()
            assert marker_problems(rel, path.read_text(encoding="utf-8")) == []


class TestMainCheckFailsClosed:
    def test_missing_target_file_fails_check(self, tmp_path: Path, monkeypatch, capsys):
        import scripts.render_metrics as rm

        monkeypatch.setattr(rm, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(rm, "TARGET_FILES", (tmp_path / "README.md",))
        monkeypatch.setattr(rm, "PORTFOLIO_METRICS_PATH", tmp_path / "absent.json")
        monkeypatch.setattr("sys.argv", ["render_metrics.py", "--check"])
        assert rm.main() == 1
        assert "does not exist" in capsys.readouterr().out

    def test_typod_block_fails_check(self, tmp_path: Path, monkeypatch, capsys):
        import scripts.render_metrics as rm

        target = tmp_path / "README.md"
        target.write_text(
            "<!-- METRICS:START:gate_sumary -->x<!-- METRICS:END -->", encoding="utf-8"
        )
        monkeypatch.setattr(rm, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(rm, "TARGET_FILES", (target,))
        monkeypatch.setattr(rm, "PORTFOLIO_METRICS_PATH", tmp_path / "absent.json")
        monkeypatch.setattr("sys.argv", ["render_metrics.py", "--check"])
        assert rm.main() == 1
        assert "gate_sumary" in capsys.readouterr().out
