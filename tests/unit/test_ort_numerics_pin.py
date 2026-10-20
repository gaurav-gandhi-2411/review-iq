"""S30: every ONNX Runtime inference session in the repo is built with the production-numerics pin (session.x64quantprecision=1)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from app.core.classify import ort_numerics as N

ROOT = Path(__file__).resolve().parents[2]
SCAN = ("app", "engine", "scripts")


def _session_sites() -> list[tuple[Path, int, str]]:
    """(file, line, source of the enclosing function or module) for every `...InferenceSession(...)` call."""
    out = []
    for top in SCAN:
        for f in sorted((ROOT / top).rglob("*.py")):
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
            parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", getattr(node.func, "id", ""))
                    == "InferenceSession"
                ):
                    scope: ast.AST = node
                    while scope in parents and not isinstance(
                        scope, ast.FunctionDef | ast.AsyncFunctionDef
                    ):
                        scope = parents[scope]
                    seg = ast.get_source_segment(src, scope) or src
                    out.append((f, node.lineno, seg))
    return out


def test_the_scan_finds_the_known_sessions() -> None:
    files = {f.relative_to(ROOT).as_posix() for f, _, _ in _session_sites()}
    # if one of these moves, the scan must still see it; a scan that finds nothing would pass vacuously
    assert {"app/core/classify/runtime.py", "scripts/onnx_eval.py", "engine/serve.py"} <= files


def test_no_inference_session_is_built_without_the_numerics_pin() -> None:
    unpinned = [
        f"{f.relative_to(ROOT)}:{line}"
        for f, line, seg in _session_sites()
        if "apply_numerics_pin(" not in seg
    ]
    assert not unpinned, f"InferenceSession built without apply_numerics_pin: {unpinned}"


def test_the_pin_is_the_documented_setting() -> None:
    assert (N.CONFIG_KEY, N.CONFIG_VALUE) == ("session.x64quantprecision", "1")


def test_apply_numerics_pin_sets_the_session_config_entry() -> None:
    ort = pytest.importorskip("onnxruntime")
    so = ort.SessionOptions()
    assert not N.is_pinned(so)
    assert N.apply_numerics_pin(so) is so and N.is_pinned(so)


def test_the_served_backend_builds_its_session_from_pinned_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ort = pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    from app.core.classify import runtime

    (tmp_path / "hf").mkdir()
    (tmp_path / "model.onnx").write_bytes(b"x")
    (tmp_path / "hf" / "tokenizer.json").write_bytes(b"x")
    seen: dict = {}

    def fake_session(path: str, so: object, providers: list) -> object:
        seen["so"] = so
        raise RuntimeError("stop after capturing the options")

    monkeypatch.setattr(ort, "InferenceSession", fake_session)
    with pytest.raises(RuntimeError, match="stop after capturing"):
        runtime.OnnxBackend(tmp_path, SimpleThresholds())
    assert N.is_pinned(seen["so"])


class SimpleThresholds:
    max_len = 128
    tasks: dict = {}  # noqa: RUF012
