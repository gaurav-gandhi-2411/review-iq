"""Unit tests for scripts/check_eval_results_reproducible.py."""

from __future__ import annotations

from scripts.check_eval_results_reproducible import strip_provenance


def _payload(**overrides: object) -> dict:
    base = {
        "generated_at": "2026-08-01T00:00:00Z",
        "git_sha": "abc123",
        "mode": "direct (local LLM)",
        "overall_score": 0.838,
        "passed": True,
        "fixtures": [
            {"id": "hi-001", "overall_score": 0.9, "latency_ms": 45, "error": None},
            {"id": "hi-002", "overall_score": 0.8, "latency_ms": 16, "error": None},
        ],
    }
    base.update(overrides)
    return base


class TestStripProvenance:
    def test_removes_generated_at_git_sha_mode(self):
        stripped = strip_provenance(_payload())
        assert "generated_at" not in stripped
        assert "git_sha" not in stripped
        assert "mode" not in stripped

    def test_removes_per_fixture_latency_ms(self):
        stripped = strip_provenance(_payload())
        assert all("latency_ms" not in f for f in stripped["fixtures"])

    def test_keeps_substantive_fields(self):
        stripped = strip_provenance(_payload())
        assert stripped["overall_score"] == 0.838
        assert stripped["passed"] is True
        assert stripped["fixtures"][0]["id"] == "hi-001"
        assert stripped["fixtures"][0]["overall_score"] == 0.9

    def test_two_runs_differing_only_in_excluded_fields_compare_equal(self):
        run_a = strip_provenance(
            _payload(generated_at="2026-08-01T00:00:00Z", git_sha="aaa", mode="direct (local LLM)")
        )
        run_b = strip_provenance(
            _payload(generated_at="2026-08-01T01:00:00Z", git_sha="bbb", mode="routed (tiered)")
        )
        assert run_a == run_b

    def test_genuine_score_difference_still_detected(self):
        committed = strip_provenance(_payload())
        regenerated = strip_provenance(
            _payload(
                fixtures=[{"id": "hi-001", "overall_score": 0.5, "latency_ms": 1, "error": None}]
            )
        )
        assert committed != regenerated

    def test_does_not_mutate_input(self):
        payload = _payload()
        strip_provenance(payload)
        assert "latency_ms" in payload["fixtures"][0]
        assert "generated_at" in payload
