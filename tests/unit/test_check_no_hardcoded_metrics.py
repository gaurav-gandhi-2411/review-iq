"""Unit tests for scripts/check_no_hardcoded_metrics.py."""

from __future__ import annotations

from pathlib import Path

from scripts.check_no_hardcoded_metrics import _is_excluded, find_violations


class TestIsExcluded:
    def test_exact_file_excluded(self):
        assert _is_excluded("plan.md") is True
        assert _is_excluded("spec.md") is True

    def test_prefix_excluded(self):
        assert _is_excluded("benchmark/results/REPORT.md") is True
        assert _is_excluded("ops/runbooks/killswitch-test.md") is True
        assert _is_excluded("docs/architecture/adr/0001-foo.md") is True
        assert _is_excluded("docs/specs/wave1-commercialization.md") is True

    def test_eval_report_md_excluded(self):
        assert _is_excluded("eval/report.md") is True

    def test_readme_not_excluded(self):
        assert _is_excluded("README.md") is False

    def test_similarly_named_file_not_excluded(self):
        # "plan_v2.md" is not "plan.md" or "plan_v1.md" -- must not match by substring.
        assert _is_excluded("plan_v2.md") is False


class TestFindViolationsMarkdown:
    def test_flags_percentage_near_keyword_in_prose(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("CI breaks if accuracy drops below 85%.\n", encoding="utf-8")
        violations = find_violations(path)
        assert len(violations) == 1
        assert violations[0][0] == 1

    def test_ignores_percentage_without_nearby_keyword(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("Approaching 80% of the storage quota.\n", encoding="utf-8")
        assert find_violations(path) == []

    def test_ignores_percentage_inside_generated_block(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text(
            "<!-- METRICS:START:extraction_table -->\n"
            "Overall accuracy: 83.8% (gate 83%)\n"
            "<!-- METRICS:END -->\n",
            encoding="utf-8",
        )
        assert find_violations(path) == []

    def test_ignores_percentage_inside_historical_block(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text(
            "<!-- METRICS:HISTORICAL -->\ngate was overall >= 85%\n<!-- /METRICS:HISTORICAL -->\n",
            encoding="utf-8",
        )
        assert find_violations(path) == []

    def test_flags_percentage_in_table_row_via_header_keyword(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text(
            "| Language | Accuracy | Gate |\n|---|---|---|\n| en | 86.2% | 80% |\n",
            encoding="utf-8",
        )
        violations = find_violations(path)
        # The data row itself doesn't say "accuracy", but the header row (same
        # contiguous table block) does -- that must be enough to flag it.
        assert any("86.2%" in text for _, text in violations)

    def test_line_numbers_preserved_after_stripping_generated_block(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text(
            "line1\n"
            "<!-- METRICS:START:x -->\nhidden 99% accuracy\n<!-- METRICS:END -->\n"
            "line5 accuracy 85% gate\n",
            encoding="utf-8",
        )
        violations = find_violations(path)
        assert violations == [(5, "line5 accuracy 85% gate")]


class TestFindViolationsHtml:
    def test_flags_percentage_inside_table_with_keyword_header(self, tmp_path: Path):
        path = tmp_path / "doc.html"
        path.write_text(
            "<table>\n"
            "<thead><tr><th>Language</th><th>Accuracy</th></tr></thead>\n"
            "<tbody><tr><td>en</td><td>86.2%</td></tr></tbody>\n"
            "</table>\n",
            encoding="utf-8",
        )
        violations = find_violations(path)
        assert any("86.2%" in text for _, text in violations)

    def test_ignores_percentage_outside_any_table_without_keyword(self, tmp_path: Path):
        path = tmp_path / "doc.html"
        path.write_text("<style>table { width: 100%; }</style>\n", encoding="utf-8")
        assert find_violations(path) == []

    def test_ignores_percentage_inside_generated_block(self, tmp_path: Path):
        path = tmp_path / "doc.html"
        path.write_text(
            "<!-- METRICS:START:extraction_table_html -->"
            "<td>accuracy 86.2%</td>"
            "<!-- METRICS:END -->\n",
            encoding="utf-8",
        )
        assert find_violations(path) == []
