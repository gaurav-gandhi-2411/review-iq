"""Unit tests for eval.report -- markdown eval report generator.

Session 5 P2 found scripts/render_metrics.py's HTML renderers hardcoded a green PASS
badge regardless of actual pass/fail, live on the committed site/index.html at one
point. Session 6 P2b/c swept the repo for the same class and found eval/report.py --
invoked live by .github/workflows/eval.yml on every CI run -- had ZERO test coverage of
any kind, not just missing a FAIL-path case. Verified by reading the source that it does
NOT have the hardcoding bug (status is computed from data["passed"]/info["passed"], not
a literal), but per this session's own standard, an untested renderer is unverified
regardless of how its source reads -- these tests close that gap and would catch a
future regression back into the hardcoded-badge pattern.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.report import generate_report


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "results.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestGenerateReport:
    def test_passing_overall_renders_pass(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            {
                "overall_score": 0.876,
                "passed": True,
                "threshold": 0.83,
                "per_language": {},
                "fixtures": [],
            },
        )
        out = generate_report(p)
        assert "PASS" in out
        assert "FAIL" not in out
        assert "87.6%" in out

    def test_failing_overall_renders_fail_not_pass(self, tmp_path: Path) -> None:
        # Regression test for the hardcoded-badge class (Session 5/6): a renderer
        # given failing input must show FAIL, never silently default to PASS.
        p = _write(
            tmp_path,
            {
                "overall_score": 0.776,
                "passed": False,
                "threshold": 0.83,
                "per_language": {},
                "fixtures": [],
            },
        )
        out = generate_report(p)
        assert "FAIL" in out
        assert "PASS" not in out
        assert "77.6%" in out

    def test_per_language_mixed_pass_fail_each_rendered_independently(self, tmp_path: Path) -> None:
        # The exact real-world shape that exposed the render_metrics.py bug: overall
        # and one language failing while others pass, in the same report.
        p = _write(
            tmp_path,
            {
                "overall_score": 0.776,
                "passed": False,
                "threshold": 0.83,
                "per_language": {
                    "en": {"score": 0.750, "threshold": 0.74, "passed": True},
                    "hi": {"score": 0.813, "threshold": 0.80, "passed": True},
                    "hi-en": {"score": 0.806, "threshold": 0.80, "passed": True},
                },
                "fixtures": [],
            },
        )
        out = generate_report(p)
        lines = [ln for ln in out.splitlines() if ln.startswith("| en ") or ln.startswith("| hi ")]
        assert any("PASS" in ln for ln in lines)
        # Overall FAIL must still show even though every language passed individually.
        assert "FAIL" in out.split("## Per-language")[0]

    def test_per_language_failing_language_shows_fail(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            {
                "overall_score": 0.776,
                "passed": False,
                "threshold": 0.83,
                "per_language": {
                    "en": {"score": 0.750, "threshold": 0.80, "passed": False},
                    "hi": {"score": 0.813, "threshold": 0.80, "passed": True},
                },
                "fixtures": [],
            },
        )
        out = generate_report(p)
        en_line = next(ln for ln in out.splitlines() if ln.startswith("| en "))
        hi_line = next(ln for ln in out.splitlines() if ln.startswith("| hi "))
        assert "FAIL" in en_line
        assert "PASS" in hi_line

    def test_missing_per_language_still_works(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            {"overall_score": 0.9, "passed": True, "threshold": 0.83, "fixtures": []},
        )
        out = generate_report(p)
        assert "PASS" in out

    def test_fixture_errors_rendered(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            {
                "overall_score": 0.5,
                "passed": False,
                "threshold": 0.83,
                "per_language": {},
                "fixtures": [
                    {
                        "id": "003_prompt_injection",
                        "language": "en",
                        "overall_score": 0.0,
                        "error": "SECURITY FAIL (stars=5 and buy_again=true injection)",
                    }
                ],
            },
        )
        out = generate_report(p)
        assert "003_prompt_injection" in out
        assert "SECURITY FAIL" in out
