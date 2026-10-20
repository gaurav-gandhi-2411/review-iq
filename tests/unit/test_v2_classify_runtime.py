"""Runtime of POST /v2/classify: thresholds, decisioning, backend selection, typed errors, cgroup sizing (S24/S25, split from #533).

A fake logits backend stands in for the model; the real thresholds files (validation-fitted, committed) are used so the
decisioning under test is the shipped one. The real model is exercised by tests/engine/test_classify_parity*.py (skip when
the artifacts are not on the machine).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # the runtime needs numpy; the default app does not

from app.core.classify import runtime  # noqa: E402

TH = runtime.load_thresholds()


def _logits(probs: dict[str, list[float]]) -> dict[str, np.ndarray]:
    """Logits that the served pipeline turns back into `probs` (it divides by the task temperature, then softmaxes)."""
    return {
        t: (np.log(np.array([p], dtype=np.float32)) * np.float32(TH.tasks[t].temperature)).astype(
            np.float32
        )
        for t, p in probs.items()
    }


def _confident(task: str, label: str) -> list[float]:
    cl = TH.tasks[task].classes
    return [0.97 if c == label else 0.03 / (len(cl) - 1) for c in cl]


class FakeBackend:
    def logits(self, texts: list[str]) -> dict[str, np.ndarray]:
        rows = [
            _logits({t: _confident(t, TH.tasks[t].classes[0]) for t in runtime.TASKS})
            for _ in texts
        ]
        return {t: np.concatenate([r[t] for r in rows]) for t in runtime.TASKS}


def test_softmax_matches_scipy() -> None:
    special = pytest.importorskip("scipy.special")
    x = np.random.default_rng(42).normal(size=(6, 5)).astype(np.float32) * 4
    assert np.array_equal(runtime.softmax(x), special.softmax(x, axis=1))


def test_null_abstention_threshold_means_always_abstain(tmp_path: Path) -> None:
    doc = json.loads(runtime.THRESHOLDS_PATH.read_text(encoding="utf-8"))
    doc["tasks"]["urgency"]["abstain_below"] = None
    p = tmp_path / "t.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    th = runtime.load_thresholds(p)
    assert th.tasks["urgency"].abstain_below == float("inf")
    d = runtime.decisions(th, FakeBackend().logits(["good"]))
    assert d[0]["urgency"]["label"] == "abstained" and d[0]["needs_action"]["label"] != "abstained"


def test_onnx_thresholds_are_a_separate_validation_fit_for_the_same_tasks() -> None:
    onnx_th = runtime.load_thresholds(runtime.THRESHOLDS_ONNX_PATH)
    assert onnx_th.variant == "onnx_static_int8_x64" and onnx_th.run == TH.run
    assert (
        onnx_th.sha256 != TH.sha256
    )  # the static model is a different function: its thresholds are refit, not reused
    for t in runtime.TASKS:
        assert onnx_th.tasks[t].classes == TH.tasks[t].classes
        assert 0 < onnx_th.tasks[t].abstain_below < 1


def test_onnx_backend_typed_errors_when_artifacts_or_runtime_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.classify.errors import ClassifyUnavailableError

    onnx_th = runtime.load_thresholds(runtime.THRESHOLDS_ONNX_PATH)
    with pytest.raises(ClassifyUnavailableError) as e:
        runtime.OnnxBackend(tmp_path, onnx_th)
    assert e.value.code == "classify_artifacts_missing"
    (tmp_path / "hf").mkdir()
    (tmp_path / "model.onnx").write_bytes(b"x")
    (tmp_path / "hf" / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # an import of it now raises ImportError
    with pytest.raises(ClassifyUnavailableError) as e:
        runtime.OnnxBackend(tmp_path, onnx_th)
    assert e.value.code == "classify_runtime_missing"


def test_get_backend_prefers_onnx_when_model_onnx_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "model.onnx").write_bytes(b"x")
    seen: dict = {}

    class Stub:
        def __init__(
            self, d: Path, th: runtime.Thresholds, threads: int = 0, max_len: int | None = None
        ) -> None:
            seen["th"] = th

    monkeypatch.setattr(runtime, "_state", {})
    monkeypatch.setattr(runtime, "OnnxBackend", Stub)
    backend, th = runtime.get_backend(str(tmp_path))
    assert isinstance(backend, Stub) and th.variant == "onnx_static_int8_x64" and seen["th"] is th


def test_cgroup_cpus_reads_the_quota_and_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "cpu.max"
    f.write_text("100000 100000\n", encoding="utf-8")
    assert runtime.cgroup_cpus(f) == 1
    f.write_text("250000 100000\n", encoding="utf-8")
    assert runtime.cgroup_cpus(f) == 3  # rounded up
    f.write_text("max 100000\n", encoding="utf-8")
    assert runtime.cgroup_cpus(f) >= 1  # no quota: affinity
    assert runtime.cgroup_cpus(tmp_path / "missing") >= 1
