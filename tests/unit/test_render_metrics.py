"""Unit tests for scripts/render_metrics.py."""

from __future__ import annotations

from pathlib import Path

from scripts.render_metrics import (
    BLOCK_RENDERERS,
    render_authenticity_table_md,
    render_extraction_table_html,
    render_extraction_table_md,
    render_file,
    render_gate_summary_md,
    render_language_table_html,
)

EXTRACTION_DATA = {
    "prompt_version": "v2.3",
    "git_sha": "deadbeef" * 5,
    "generated_at": "2026-07-30T00:00:00Z",
    "mode": "direct (local LLM)",
    "tiered_routing_enabled_at_runtime": True,
    "overall_score": 0.838,
    "overall_ci_95": {"n": 49, "lower": 0.71, "upper": 0.92},
    "threshold": 0.83,
    "passed": True,
    "per_language": {
        "en": {
            "score": 0.862,
            "n": 27,
            "ci_95": {"lower": 0.68, "upper": 0.95},
            "threshold": 0.8,
            "passed": True,
        },
        "hi": {
            "score": 0.807,
            "n": 7,
            "ci_95": {"lower": 0.44, "upper": 0.96},
            "threshold": 0.8,
            "passed": True,
        },
        "hi-en": {
            "score": 0.809,
            "n": 15,
            "ci_95": {"lower": 0.56, "upper": 0.93},
            "threshold": 0.8,
            "passed": True,
        },
    },
}

AUTHENTICITY_DATA = {
    "mode": "historical (reconstructed)",
    "provenance_note": "reconstructed from published counts",
    "n": 40,
    "confusion_matrix": {"tp": 21, "fp": 0, "fn": 0, "tn": 19},
    "precision": {"value": 1.0, "n": 21, "ci_95": {"lower": 0.845, "upper": 1.0}},
    "recall": {"value": 1.0, "n": 21, "ci_95": {"lower": 0.845, "upper": 1.0}},
    "f1": {"value": 1.0, "n": 40, "ci_95": {"lower": 0.912, "upper": 1.0}},
    "precision_gate": 0.80,
    "gate_passed": True,
}


class TestRenderExtractionTableMd:
    def test_contains_all_languages_and_overall(self):
        out = render_extraction_table_md(EXTRACTION_DATA)
        assert "en |" in out
        assert "hi-en |" in out
        assert "**Overall**" in out
        assert "83.8%" in out
        assert "PASS" in out

    def test_language_order_matches_repo_convention(self):
        out = render_extraction_table_md(EXTRACTION_DATA)
        # en, then hi-en, then hi -- not alphabetical.
        assert out.index("| en |") < out.index("| hi-en |") < out.index("| hi |")

    def test_fail_status_shown_when_not_passed(self):
        data = {**EXTRACTION_DATA, "passed": False}
        out = render_extraction_table_md(data)
        assert "**FAIL**" in out or "FAIL |" in out

    def test_routing_note_present_when_tiered_routing_on(self):
        out = render_extraction_table_md(EXTRACTION_DATA)
        assert "Tiered routing is ON" in out

    def test_routing_note_absent_variant_when_off(self):
        data = {**EXTRACTION_DATA, "tiered_routing_enabled_at_runtime": False}
        out = render_extraction_table_md(data)
        assert "Tiered routing was OFF" in out


class TestRenderAuthenticityTableMd:
    def test_contains_metrics_and_provenance(self):
        out = render_authenticity_table_md(AUTHENTICITY_DATA)
        assert "Precision" in out
        assert "1.000" in out
        assert "0.845" in out
        assert "reconstructed from published counts" in out

    def test_gate_not_met_wording(self):
        data = {**AUTHENTICITY_DATA, "gate_passed": False}
        out = render_authenticity_table_md(data)
        assert "NOT met" in out


class TestRenderGateSummaryMd:
    def test_reflects_thresholds(self):
        out = render_gate_summary_md(EXTRACTION_DATA)
        assert "80%" in out
        assert "83%" in out


class TestRenderExtractionTableHtml:
    def test_renders_a_row_per_language_plus_overall(self):
        out = render_extraction_table_html(EXTRACTION_DATA)
        assert out.count("<tr") == 4  # 3 languages + overall
        assert "86.2%" in out
        assert "83.8%" in out


class TestRenderLanguageTableHtml:
    def test_renders_three_rows(self):
        out = render_language_table_html(EXTRACTION_DATA)
        assert out.count("<tr") == 3
        assert "Devanagari" in out


class TestRenderFile:
    def test_replaces_known_block_and_leaves_rest_untouched(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "doc.md"
        target.write_text(
            "before\n<!-- METRICS:START:gate_summary -->OLD<!-- METRICS:END -->\nafter\n",
            encoding="utf-8",
        )
        # Point the renderer at our in-memory test data instead of the real JSON files.
        monkeypatch.setitem(BLOCK_RENDERERS, "gate_summary", lambda: "NEW")
        new_content, changed = render_file(target)
        assert changed is True
        assert (
            new_content
            == "before\n<!-- METRICS:START:gate_summary -->NEW<!-- METRICS:END -->\nafter\n"
        )

    def test_unknown_block_name_left_untouched(self, tmp_path: Path):
        target = tmp_path / "doc.md"
        original = "<!-- METRICS:START:not_a_real_block -->stuff<!-- METRICS:END -->"
        target.write_text(original, encoding="utf-8")
        new_content, changed = render_file(target)
        assert changed is False
        assert new_content == original

    def test_no_markers_means_no_change(self, tmp_path: Path):
        target = tmp_path / "doc.md"
        target.write_text("plain text, no markers here\n", encoding="utf-8")
        new_content, changed = render_file(target)
        assert changed is False
