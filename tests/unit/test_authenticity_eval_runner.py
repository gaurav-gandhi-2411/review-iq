"""Unit tests for eval/authenticity/runner.py's metrics + Wilson-CI JSON writer.

No LLM calls — these test pure confusion-matrix arithmetic and file I/O.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval.authenticity.runner import compute_metrics, write_results


class TestComputeMetrics:
    def test_perfect_classifier(self):
        m = compute_metrics(tp=21, fp=0, fn=0)
        assert m == {"precision": 1.0, "recall": 1.0, "f1": 1.0}

    def test_zero_positives_predicted(self):
        m = compute_metrics(tp=0, fp=0, fn=5)
        assert m["precision"] == 0.0
        assert m["recall"] == 0.0
        assert m["f1"] == 0.0

    def test_partial_precision_recall(self):
        m = compute_metrics(tp=3, fp=1, fn=2)
        assert m["precision"] == 0.75
        assert m["recall"] == 0.6
        assert 0.0 < m["f1"] < 1.0


class TestWriteResults:
    def test_writes_valid_json_with_expected_keys(self, tmp_path: Path):
        out_path = tmp_path / "authenticity_latest.json"
        write_results(tp=21, fp=0, fn=0, tn=19, mode="test-mode", out_path=out_path)

        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))

        assert data["mode"] == "test-mode"
        assert data["n"] == 40
        assert data["confusion_matrix"] == {"tp": 21, "fp": 0, "fn": 0, "tn": 19}
        assert data["precision"]["value"] == 1.0
        assert data["precision"]["n"] == 21
        assert 0.0 < data["precision"]["ci_95"]["lower"] < 1.0
        assert data["precision"]["ci_95"]["upper"] == pytest.approx(1.0)
        assert data["recall"]["n"] == 21
        assert data["f1"]["n"] == 40
        assert data["gate_passed"] is True
        assert data["generated_at"].endswith("Z")

    def test_gate_fails_below_precision_threshold(self, tmp_path: Path):
        out_path = tmp_path / "authenticity_latest.json"
        # precision = 5/10 = 0.5, below the 0.80 gate.
        write_results(tp=5, fp=5, fn=0, tn=30, mode="test-mode", out_path=out_path)
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["gate_passed"] is False

    def test_creates_parent_directory(self, tmp_path: Path):
        out_path = tmp_path / "nested" / "dir" / "authenticity_latest.json"
        write_results(tp=1, fp=0, fn=0, tn=1, mode="test-mode", out_path=out_path)
        assert out_path.exists()

    def test_provenance_note_included_when_given(self, tmp_path: Path):
        out_path = tmp_path / "authenticity_latest.json"
        write_results(
            tp=1,
            fp=0,
            fn=0,
            tn=1,
            mode="test-mode",
            provenance_note="reconstructed from historical counts",
            out_path=out_path,
        )
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["provenance_note"] == "reconstructed from historical counts"
