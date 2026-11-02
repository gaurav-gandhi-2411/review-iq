"""CLASSIFY_MAX_LEN: a bounded, opt-in override of the served input length, for capacity probes only (S30, Amendment 16)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.core.classify import runtime
from app.core.classify.errors import ClassifyUnavailableError


def test_unset_or_empty_means_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLASSIFY_MAX_LEN", raising=False)
    assert runtime.max_len_override() is None
    monkeypatch.setenv("CLASSIFY_MAX_LEN", "  ")
    assert runtime.max_len_override() is None


@pytest.mark.parametrize("raw", ["16", "256", "512"])
def test_values_inside_the_bounds_are_accepted(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("CLASSIFY_MAX_LEN", raw)
    assert runtime.max_len_override() == int(raw)


@pytest.mark.parametrize("raw", ["15", "513", "0", "-1", "abc", "128.5"])
def test_values_outside_the_bounds_or_not_integers_refuse_to_start(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("CLASSIFY_MAX_LEN", raw)
    with pytest.raises(ClassifyUnavailableError) as e:
        runtime.max_len_override()
    assert e.value.code == "classify_bad_config"


def test_the_backend_truncates_at_the_override_and_reports_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ort = pytest.importorskip("onnxruntime")
    tk = pytest.importorskip("tokenizers")
    (tmp_path / "hf").mkdir()
    (tmp_path / "model.onnx").write_bytes(b"x")
    (tmp_path / "hf" / "tokenizer.json").write_bytes(b"x")
    seen: dict = {}

    class FakeTok:
        def enable_truncation(self, max_length: int) -> None:
            seen["max_length"] = max_length

    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: object())
    monkeypatch.setattr(tk.Tokenizer, "from_file", staticmethod(lambda p: FakeTok()))

    class Th:
        max_len = 128
        tasks: dict = {}  # noqa: RUF012

    b = runtime.OnnxBackend(tmp_path, Th(), max_len=256)
    assert (b.max_len, seen["max_length"]) == (256, 256)
    b = runtime.OnnxBackend(tmp_path, Th())
    assert (b.max_len, seen["max_length"]) == (128, 128)  # no override: the contract length
