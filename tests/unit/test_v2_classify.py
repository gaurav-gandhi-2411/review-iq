"""POST /v2/classify: flag-off 404, response contract, abstention, rare-class decisioning, validation, 503 (S24 A6).

A fake logits backend stands in for the model; the real thresholds file (validation-fitted, committed) is used so the
decisioning under test is the shipped one. The real model is exercised by tests/engine/test_classify_parity.py (skips
when the artifacts are not on the machine).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip(
    "numpy"
)  # the runtime (and so the endpoint's happy path) needs numpy; the default app does not

import httpx  # noqa: E402
from app.auth.api_key import ApiKeyContext, require_api_key  # noqa: E402
from app.core.classify import runtime  # noqa: E402
from app.main import create_app  # noqa: E402

_CTX = ApiKeyContext(org_id="test-org", api_key_id="k", key_name="k", usage_record_id="u")
TH = runtime.load_thresholds()
ROOT = Path(__file__).resolve().parents[2]


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
    """Text starting with 'flat' -> uniform (abstains everywhere); 'rare' -> urgency=high at 0.2 with medium at 0.7."""

    def logits(self, texts: list[str]) -> dict[str, np.ndarray]:
        rows = []
        for tx in texts:
            probs = {t: _confident(t, TH.tasks[t].classes[0]) for t in runtime.TASKS}
            if tx.startswith("flat"):
                probs = {
                    t: [1 / len(s.classes)] * len(s.classes)
                    for t, s in TH.tasks.items()
                    if t in runtime.TASKS
                }
            if tx.startswith("rare"):
                cl = TH.tasks["urgency"].classes
                probs["urgency"] = [{"high": 0.2, "medium": 0.7}.get(c, 0.1) for c in cl]
            rows.append(_logits(probs))
        return {t: np.concatenate([r[t] for r in rows]) for t in runtime.TASKS}


def _settings(enabled: bool, artifact_dir: str = "/nonexistent") -> SimpleNamespace:
    return SimpleNamespace(
        enable_v2_classify=enabled, classify_artifact_dir=artifact_dir, classify_torch_threads=0
    )


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[require_api_key] = lambda: _CTX
    monkeypatch.setattr(runtime, "_state", {"backend": FakeBackend(), "th": TH})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _enable(
    monkeypatch: pytest.MonkeyPatch, enabled: bool = True, artifact_dir: str = "/nonexistent"
) -> None:
    monkeypatch.setattr(
        "app.api.v2.classify.get_settings", lambda: _settings(enabled, artifact_dir)
    )


def _body(*texts: str) -> dict:
    return {"reviews": [{"id": f"r{i}", "text": t} for i, t in enumerate(texts)]}


@pytest.mark.asyncio
async def test_flag_off_is_404_like_sibling_flagged_endpoints(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, enabled=False)
    r = await client.post("/v2/classify", json=_body("anything"))
    assert (r.status_code, r.json()) == (404, {"detail": "Not found."})


def test_flag_defaults_off() -> None:
    from app.core.config import Settings

    assert Settings().enable_v2_classify is False


@pytest.mark.asyncio
async def test_contract_confident_review(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    r = await client.post("/v2/classify", json=_body("good", "also good"))
    assert r.status_code == 200
    d = r.json()
    assert d["decision"]["mode"] == "threshold" and d["decision"]["thresholds_sha256"] == TH.sha256
    assert d["model"]["variant"] == "int8_ffn_full" and d["model"]["run"] == "run2"
    assert [x["id"] for x in d["results"]] == ["r0", "r1"] and [
        x["index"] for x in d["results"]
    ] == [0, 1]
    for item in d["results"]:
        assert set(item["tasks"]) == set(runtime.TASKS)
        for t, td in item["tasks"].items():
            assert td["label"] == TH.tasks[t].classes[0] == td["candidate_label"]
            assert td["abstained"] is False and td["calibrated"] is True
            assert td["confidence"] == pytest.approx(0.97, abs=1e-6)
            assert sum(td["probabilities"].values()) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.asyncio
async def test_abstains_instead_of_forcing_argmax(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    d = (await client.post("/v2/classify", json=_body("flat text"))).json()
    for td in d["results"][0]["tasks"].values():
        assert td["label"] == "abstained" and td["abstained"] is True
        assert td["candidate_label"] in td["probabilities"]


@pytest.mark.asyncio
async def test_rare_class_is_threshold_decided_not_argmax(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """urgency=high at p=0.2 is flagged by the validation threshold (0.144) although medium (0.7) is the argmax; the
    final label is still 'abstained' (0.7 < the urgency abstention threshold) and the flag stays visible."""
    _enable(monkeypatch)
    td = (await client.post("/v2/classify", json=_body("rare case"))).json()["results"][0]["tasks"][
        "urgency"
    ]
    assert td["candidate_label"] == "high" and td["probability"] == pytest.approx(0.2, abs=1e-6)
    assert td["label"] == "abstained"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"reviews": []},
        {"reviews": [{"text": "x"}] * 33},
        {"reviews": [{"text": "   "}]},
        {"reviews": [{"text": "x" * 5001}]},
        {"reviews": [{"id": "i" * 129, "text": "x"}]},
        {},
    ],
)
async def test_validation_errors_are_422(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    _enable(monkeypatch)
    assert (await client.post("/v2/classify", json=body)).status_code == 422


@pytest.mark.asyncio
async def test_503_with_machine_code_when_artifacts_missing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _enable(monkeypatch, artifact_dir=str(tmp_path / "no-such-dir"))
    monkeypatch.setattr(runtime, "_state", {})  # drop the fake: force the real lazy loader
    r = await client.post("/v2/classify", json=_body("x"))
    assert r.status_code == 503
    assert r.json()["code"] == "classify_artifacts_missing" and r.json()["correlation_id"]
    assert r.headers["retry-after"] == "60"


@pytest.mark.asyncio
async def test_unexpected_failure_is_typed_500_without_leaking(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)

    class Boom:
        def logits(self, texts: list[str]) -> dict:
            raise ValueError("secret /home/x/path")

    monkeypatch.setattr(runtime, "_state", {"backend": Boom(), "th": TH})
    r = await client.post("/v2/classify", json=_body("x"))
    assert r.status_code == 500 and r.json()["code"] == "classify_internal_error"
    assert "secret" not in r.text


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
    assert onnx_th.variant == "onnx_static_int8" and onnx_th.run == TH.run
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
        def __init__(self, d: Path, th: runtime.Thresholds, threads: int = 0) -> None:
            seen["th"] = th

    monkeypatch.setattr(runtime, "_state", {})
    monkeypatch.setattr(runtime, "OnnxBackend", Stub)
    backend, th = runtime.get_backend(str(tmp_path))
    assert isinstance(backend, Stub) and th.variant == "onnx_static_int8" and seen["th"] is th


def test_cgroup_cpus_reads_the_quota_and_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "cpu.max"
    f.write_text("100000 100000\n", encoding="utf-8")
    assert runtime.cgroup_cpus(f) == 1
    f.write_text("250000 100000\n", encoding="utf-8")
    assert runtime.cgroup_cpus(f) == 3  # rounded up
    f.write_text("max 100000\n", encoding="utf-8")
    assert runtime.cgroup_cpus(f) >= 1  # no quota: affinity
    assert runtime.cgroup_cpus(tmp_path / "missing") >= 1


def test_default_app_import_needs_neither_torch_nor_the_runtime() -> None:
    code = (
        "import sys, app.main; "
        "assert 'torch' not in sys.modules and 'app.core.classify.runtime' not in sys.modules"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )  # noqa: S603
    assert r.returncode == 0, r.stderr
