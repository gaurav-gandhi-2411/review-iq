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


class TestMain:
    """main() had no tests. These drive it with a fake eval.runner (no LLM, no network)."""

    @staticmethod
    def _setup(tmp_path, monkeypatch, *, committed, regenerated, returncode=0):
        import json
        import types

        import scripts.check_eval_results_reproducible as mod

        latest = tmp_path / "latest.json"
        results = tmp_path / "results.json"
        latest.write_text(json.dumps(committed), encoding="utf-8")
        results.write_text(json.dumps(committed), encoding="utf-8")
        monkeypatch.setattr(mod, "LATEST_RESULTS_PATH", latest)
        monkeypatch.setattr(mod, "RESULTS_PATH", results)
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")

        def fake_run(*_a, **_k):
            # The real runner overwrites both files with what it regenerated.
            for path in (latest, results):
                path.write_text(json.dumps(regenerated), encoding="utf-8")
            return types.SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        return mod

    def test_identical_regeneration_passes(self, tmp_path, monkeypatch):
        mod = self._setup(tmp_path, monkeypatch, committed=_payload(), regenerated=_payload())
        assert mod.main() == 0

    def test_hand_edited_score_is_caught(self, tmp_path, monkeypatch):
        mod = self._setup(
            tmp_path,
            monkeypatch,
            committed=_payload(overall_score=0.99),  # what a hand-edit would commit
            regenerated=_payload(overall_score=0.838),
        )
        assert mod.main() == 1

    def test_runner_crash_fails(self, tmp_path, monkeypatch):
        mod = self._setup(
            tmp_path, monkeypatch, committed=_payload(), regenerated=_payload(), returncode=3
        )
        assert mod.main() == 1

    def test_refuses_to_run_without_replay_mode(self, tmp_path, monkeypatch):
        mod = self._setup(tmp_path, monkeypatch, committed=_payload(), regenerated=_payload())
        monkeypatch.delenv("EVAL_CASSETTE_MODE")
        called = []
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: called.append(a))
        assert mod.main() == 1
        assert called == []  # never reached the runner, so nothing was overwritten

    def test_KNOWN_GAP_failing_accuracy_gate_still_passes_reproducibility(
        self, tmp_path, monkeypatch
    ):
        """Pins a documented, deliberate behaviour: runner exit 1 (accuracy gate FAIL) counts as
        a valid regeneration, so this check proves the files are machine-generated -- NOT that
        the eval passes. ci.yml has no PR-time accuracy gate; eval.yml runs post-merge only."""
        failing = _payload(overall_score=0.5, passed=False)
        mod = self._setup(
            tmp_path, monkeypatch, committed=failing, regenerated=failing, returncode=1
        )
        assert mod.main() == 0
