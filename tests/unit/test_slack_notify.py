"""Unit tests for eval.slack_notify payload builder."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eval.slack_notify import build_payload


@pytest.fixture
def results_file(tmp_path: Path) -> Path:
    payload = {
        "overall_score": 0.872,
        "threshold": 0.85,
        "passed": True,
        "per_language": {
            "en": {"score": 0.91, "threshold": 0.80, "passed": True},
            "hi": {"score": 0.885, "threshold": 0.80, "passed": True},
            "hi-en": {"score": 0.806, "threshold": 0.80, "passed": True},
        },
        "fixtures": [],
    }
    p = tmp_path / "results.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture
def failing_results_file(tmp_path: Path) -> Path:
    payload = {
        "overall_score": 0.72,
        "threshold": 0.85,
        "passed": False,
        "per_language": {
            "en": {"score": 0.72, "threshold": 0.80, "passed": False},
        },
        "fixtures": [],
    }
    p = tmp_path / "results.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestBuildPayload:
    def test_header_contains_pass(self, results_file: Path) -> None:
        payload = build_payload(results_file, "https://example.com/run/1")
        header_block = payload["blocks"][0]
        assert header_block["type"] == "header"
        assert "PASS" in header_block["text"]["text"]

    def test_header_contains_fail(self, failing_results_file: Path) -> None:
        payload = build_payload(failing_results_file, "https://example.com/run/2")
        assert "FAIL" in payload["blocks"][0]["text"]["text"]

    def test_overall_score_in_fields(self, results_file: Path) -> None:
        payload = build_payload(results_file, "")
        section = payload["blocks"][1]
        assert section["type"] == "section"
        combined = " ".join(f["text"] for f in section["fields"])
        assert "87.2%" in combined

    def test_per_language_in_fields(self, results_file: Path) -> None:
        payload = build_payload(results_file, "")
        section = payload["blocks"][1]
        combined = " ".join(f["text"] for f in section["fields"])
        assert "en" in combined
        assert "hi-en" in combined
        assert "hi" in combined

    def test_run_url_in_action_button(self, results_file: Path) -> None:
        url = "https://github.com/owner/repo/actions/runs/999"
        payload = build_payload(results_file, url)
        actions = payload["blocks"][2]
        assert actions["type"] == "actions"
        assert actions["elements"][0]["url"] == url

    def test_blocks_is_valid_json(self, results_file: Path) -> None:
        payload = build_payload(results_file, "")
        json.dumps(payload)  # must not raise

    def test_missing_per_language_still_works(self, tmp_path: Path) -> None:
        data = {"overall_score": 0.9, "threshold": 0.85, "passed": True, "fixtures": []}
        p = tmp_path / "results.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        payload = build_payload(p, "")
        assert payload["blocks"][0]["type"] == "header"
