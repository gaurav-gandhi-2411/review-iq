"""Unit tests for the authenticity eval's cassette replay + strict scoring.

Zero live network: sockets are patched to fail in the replay tests, and no test uses record mode.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import app.core.providers.cassette as cassette_module
import httpx
import pytest
from app.core.authenticity import engine
from app.core.config import Settings
from eval.authenticity import replay as replay_mod
from eval.authenticity import runner
from eval.authenticity.scoring import (
    MIN_POSITIVES_FOR_CLAIM,
    labelled_report,
    predictions_only_report,
    strict_metrics,
)

MODEL = "mock-large-model"
TEXT = "Bahut accha product hai, battery backup 2 din chalta hai. Would buy again."
RAW_OK = json.dumps({"score": 0.9, "flags": [], "reasoning": "specific personal detail"})
RAW_FLAG = json.dumps({"score": 0.1, "flags": ["promotional_tone"], "reasoning": "ad copy"})


def _settings() -> Settings:
    return Settings(
        GROQ_API_KEY="test-key", GROQ_MODEL_LARGE=MODEL, GROQ_MODEL_SMALL="mock-small-model"
    )


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail any attempt to resolve a host / open a connection / send an httpx request.

    Returns the list of recorded attempts (the engine swallows exceptions into a neutral row,
    so tests must assert this list is empty rather than rely on the raise propagating).
    socket.socket.connect is deliberately NOT patched: asyncio's Windows loop needs it for its
    self-pipe socketpair.
    """
    attempts: list[str] = []

    def _boom(*_a: Any, **_k: Any) -> None:
        attempts.append("network")
        raise AssertionError("network access attempted in replay mode")

    async def _aboom(*_a: Any, **_k: Any) -> None:
        _boom()

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(httpx.Client, "send", _boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", _aboom)
    return attempts


@pytest.fixture
def cassette_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolated cassette file + env; monkeypatch restores CASSETTES_PATH and the env var."""
    path = tmp_path / "authenticity_cassettes.json"
    monkeypatch.setattr(cassette_module, "CASSETTES_PATH", path)
    monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")
    return path


def _write_store(path: Path, entries: dict[str, str]) -> None:
    store = {k: {"raw": raw, "tokens_in": 500, "tokens_out": 40} for k, raw in entries.items()}
    path.write_text(json.dumps(store), encoding="utf-8")


class TestCassetteKey:
    def test_stable_for_same_inputs(self) -> None:
        assert replay_mod.authenticity_cassette_key(TEXT, MODEL) == (
            replay_mod.authenticity_cassette_key(TEXT, MODEL)
        )

    def test_changes_with_model(self) -> None:
        assert replay_mod.authenticity_cassette_key(TEXT, "model-a") != (
            replay_mod.authenticity_cassette_key(TEXT, "model-b")
        )

    def test_changes_with_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = replay_mod.authenticity_cassette_key(TEXT, MODEL)
        monkeypatch.setattr(
            replay_mod,
            "build_authenticity_prompt",
            lambda text, lang: ("edited system prompt", f"edited task: {text}"),
        )
        assert replay_mod.authenticity_cassette_key(TEXT, MODEL) != before

    def test_changes_with_review_text(self) -> None:
        assert replay_mod.authenticity_cassette_key(TEXT, MODEL) != (
            replay_mod.authenticity_cassette_key(TEXT + " extra", MODEL)
        )

    def test_matches_key_the_provider_computes(self) -> None:
        """The runner's key must equal what GroqProvider looks up, or replay would always miss."""
        from app.core.language import detect_language
        from app.core.prompts.authenticity import build_authenticity_prompt
        from app.core.providers.groq import _make_cassette_key

        sys_p, user_p = build_authenticity_prompt(TEXT, detect_language(TEXT))
        assert replay_mod.authenticity_cassette_key(TEXT, MODEL) == _make_cassette_key(
            MODEL, sys_p, user_p
        )


class TestReplay:
    @pytest.mark.asyncio
    async def test_hit_returns_recorded_output_without_network(
        self, cassette_env: Path, no_network: list[str]
    ) -> None:
        key = replay_mod.authenticity_cassette_key(TEXT, MODEL)
        _write_store(cassette_env, {key: RAW_FLAG})
        result = await engine.score_single(TEXT, settings=_settings())
        assert result.llm_signal_ok is True
        assert result.model_used == MODEL
        assert "promotional_tone" in [f.value for f in result.flags]
        assert no_network == []

    @pytest.mark.asyncio
    async def test_miss_never_reaches_network(
        self, cassette_env: Path, no_network: list[str]
    ) -> None:
        _write_store(cassette_env, {})
        result = await engine.score_single(TEXT, settings=_settings())
        # engine swallows the provider's RuntimeError into a neutral row flagged not-ok; the
        # runner turns that into INVALID RUN. The point here: no socket was opened.
        assert result.llm_signal_ok is False
        assert no_network == []

    def test_preflight_raises_on_missing_key(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        _write_store(path, {})
        with pytest.raises(replay_mod.CassetteMissError, match="1/1"):
            replay_mod.preflight([TEXT], MODEL, path)

    def test_preflight_absent_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(replay_mod.CassetteMissError):
            replay_mod.preflight([TEXT], MODEL, tmp_path / "nope.json")

    def test_preflight_ok_when_present(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        key = replay_mod.authenticity_cassette_key(TEXT, MODEL)
        _write_store(path, {key: RAW_OK})
        assert replay_mod.preflight([TEXT], MODEL, path) == {"0": key}

    def test_prompt_change_invalidates_cassette(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "c.json"
        _write_store(path, {replay_mod.authenticity_cassette_key(TEXT, MODEL): RAW_OK})
        monkeypatch.setattr(
            replay_mod,
            "build_authenticity_prompt",
            lambda text, lang: ("new system prompt", text),
        )
        with pytest.raises(replay_mod.CassetteMissError):
            replay_mod.preflight([TEXT], MODEL, path)

    def test_entry_count(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        assert replay_mod.cassette_entry_count(path) == 0
        _write_store(path, {"a": RAW_OK, "b": RAW_OK})
        assert replay_mod.cassette_entry_count(path) == 2

    def test_configure_rejects_live_mode(self) -> None:
        with pytest.raises(ValueError):
            replay_mod.configure_cassettes("live")  # type: ignore[arg-type]

    def test_default_cassette_file_is_separate(self) -> None:
        assert replay_mod.AUTHENTICITY_CASSETTES_PATH.name == "authenticity_cassettes.json"
        assert replay_mod.AUTHENTICITY_CASSETTES_PATH != cassette_module._DEFAULT_CASSETTES_PATH


class TestRunnerEndToEnd:
    """run_eval against synthetic cassettes -- exercises corpus load, provenance, no network."""

    def _seed(self, path: Path, items: list[dict[str, Any]], raw: str) -> None:
        _write_store(
            path, {replay_mod.authenticity_cassette_key(i["text"], MODEL): raw for i in items}
        )

    @pytest.mark.asyncio
    async def test_held_out_emits_predictions_only(
        self,
        cassette_env: Path,
        no_network: list[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "get_settings", _settings)
        items = runner.load_held_out_fixtures()
        assert len(items) == 106
        self._seed(cassette_env, items, RAW_OK)
        out = tmp_path / "out.json"
        code = await runner.run_eval("held-out", "replay", False, out, cassette_env)
        assert code == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["n"] == 106
        assert data["runs"] == 1 and data["single_run"] is True
        assert data["mode"] == "replay" and data["corpus"] == "held-out"
        assert data["labels_source"].startswith("none:")
        assert data["cassette"]["entry_count"] == len({i["text"] for i in items})
        assert data["models"]["authenticity_model"] == MODEL
        assert data["report"]["precision"] is None and data["report"]["f1"] is None
        assert data["report"]["claim_supported"] is False
        assert "flag_rate" in data["report"]
        assert all(r["cassette_key"] and r["tokens_in"] == 500 for r in data["records"])

    @pytest.mark.asyncio
    async def test_missing_cassettes_exit_3_and_write_nothing(
        self,
        cassette_env: Path,
        no_network: list[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "get_settings", _settings)
        _write_store(cassette_env, {})
        out = tmp_path / "out.json"
        code = await runner.run_eval("held-out", "replay", False, out, cassette_env)
        assert code == 3
        assert not out.exists()

    @pytest.mark.asyncio
    async def test_labeled_corpus_claim_unsupported(
        self,
        cassette_env: Path,
        no_network: list[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(runner, "get_settings", _settings)
        items = runner.load_fixtures()
        self._seed(cassette_env, items, RAW_FLAG)
        out = tmp_path / "out.json"
        await runner.run_eval("labeled", "replay", False, out, cassette_env)
        report = json.loads(out.read_text(encoding="utf-8"))["report"]
        assert report["n_pos"] == 21
        assert report["claim_supported"] is False
        assert "n_pos=21" in report["claim_reason"]

    def test_run_never_defaults_to_quarantined_file(self) -> None:
        assert runner.RESULTS_DIR / "authenticity_latest.json" != (
            runner.RESULTS_DIR / "authenticity_labeled_replay.json"
        )


class TestScoring:
    def test_strict_metrics_undefined_is_none(self) -> None:
        assert strict_metrics(0, 0, 0) == {"precision": None, "recall": None, "f1": None}

    def test_tiny_synthetic_metrics(self) -> None:
        y_true = [True, True, False, False, True]
        y_pred = [True, False, True, False, True]
        r = labelled_report(y_true, y_pred, is_held_out=True, n_resamples=200)
        assert r["confusion_matrix"] == {"tp": 2, "fp": 1, "fn": 1, "tn": 1}
        assert r["precision"]["value"] == pytest.approx(2 / 3)
        assert r["recall"]["value"] == pytest.approx(2 / 3)
        assert r["f1"]["value"] == pytest.approx(2 / 3)
        assert r["base_rate_positive"] == pytest.approx(3 / 5)
        lo, hi = r["precision"]["ci_95"]["lower"], r["precision"]["ci_95"]["upper"]
        assert 0.0 <= lo <= 2 / 3 <= hi <= 1.0

    def test_bootstrap_is_deterministic(self) -> None:
        y_true = [True, False] * 20
        y_pred = [True, True, False, False] * 10
        a = labelled_report(y_true, y_pred, is_held_out=True, n_resamples=300)
        b = labelled_report(y_true, y_pred, is_held_out=True, n_resamples=300)
        assert a == b
        assert a["bootstrap"] == {"n_resamples": 300, "seed": 42}

    def test_few_positives_claim_unsupported(self) -> None:
        r = labelled_report(
            [True] * 5 + [False] * 95, [True] * 5 + [False] * 95, is_held_out=True, n_resamples=50
        )
        assert r["n_pos"] == 5
        assert r["claim_supported"] is False
        assert "n_pos=5" in r["claim_reason"]

    def test_enough_positives_claim_supported_only_if_held_out(self) -> None:
        n = MIN_POSITIVES_FOR_CLAIM
        y = [True] * n + [False] * 10
        assert labelled_report(y, y, is_held_out=True, n_resamples=50)["claim_supported"] is True
        r = labelled_report(y, y, is_held_out=False, n_resamples=50)
        assert r["claim_supported"] is False
        assert "in-sample" in r["claim_reason"]

    def test_all_negative_predictions_no_positives_does_not_crash(self) -> None:
        r = labelled_report([False] * 10, [False] * 10, is_held_out=True, n_resamples=50)
        assert r["precision"]["value"] is None and r["precision"]["ci_95"] is None
        assert r["recall"]["value"] is None and r["recall"]["ci_95"] is None
        assert r["f1"]["value"] is None
        assert r["base_rate_positive"] == 0.0
        assert r["claim_supported"] is False

    def test_all_negative_predictions_with_positives(self) -> None:
        r = labelled_report(
            [True] * 3 + [False] * 7, [False] * 10, is_held_out=True, n_resamples=50
        )
        assert r["precision"]["value"] is None  # nothing predicted positive
        assert r["recall"]["value"] == 0.0
        assert r["f1"]["value"] == 0.0

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            labelled_report([True], [True, False], is_held_out=True)

    def test_predictions_only_report_is_not_precision(self) -> None:
        r = predictions_only_report([True, False, False, False])
        assert r["flag_rate"] == 0.25
        assert r["precision"] is None and r["recall"] is None and r["f1"] is None
        assert r["claim_supported"] is False
        assert "not a precision" in r["claim_reason"]

    def test_predictions_only_empty(self) -> None:
        assert predictions_only_report([])["flag_rate"] is None


class TestQuarantine:
    def test_latest_json_is_marked_do_not_publish(self) -> None:
        data = json.loads((runner.RESULTS_DIR / "authenticity_latest.json").read_text("utf-8"))
        assert data["status"] == "quarantined"
        assert data["do_not_publish"] is True
        assert data["quarantine_reason"]
