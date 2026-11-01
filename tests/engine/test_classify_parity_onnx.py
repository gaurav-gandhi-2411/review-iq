"""Static-int8 ONNX served path vs the offline evaluation on the SEALED set (S25). Skips cleanly without the artifacts or texts.

Needs: onnxruntime + tokenizers + scikit-learn, the artifacts (CLASSIFY_ONNX_ARTIFACTS, default
D:/ml-cache/s25/classify_artifacts_onnx: model.onnx and hf/) and the private review texts (CLASSIFY_PARITY_TEXTS, default
D:/ml-cache/review-intent/scale.jsonl). The offline reference (the pred files) is scored one review per forward with the
transformers tokenizer; ONNX Runtime int8 kernels are CPU-specific, so a different machine may differ on a borderline item.
Review text is never written.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("sklearn")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

from scripts import classify_parity as P  # noqa: E402

ARTIFACTS = Path(
    os.environ.get("CLASSIFY_ONNX_ARTIFACTS", "D:/ml-cache/s25/classify_artifacts_onnx")
)
TEXTS = Path(os.environ.get("CLASSIFY_PARITY_TEXTS", "D:/ml-cache/review-intent/scale.jsonl"))


@pytest.fixture(autouse=True)
def onnx_config() -> Iterator[None]:
    before = dict(P.CFG)
    P.CFG.update(P.CFGS["onnx"])
    yield
    P.CFG.clear()
    P.CFG.update(before)


def test_thresholds_file_equals_offline_recomputation_bit_for_bit() -> None:
    assert P.thresholds_bit_for_bit()["equal"] is True


@pytest.fixture(scope="module")
def result() -> dict:
    if not (ARTIFACTS / "model.onnx").is_file() or not TEXTS.is_file():
        pytest.skip("ONNX artifacts or the private review texts are not on this machine")
    P.CFG.update(P.CFGS["onnx"])
    try:
        return P.run(ARTIFACTS, TEXTS)
    finally:
        P.CFG.update(P.CFGS["torch"])


def test_served_one_review_per_forward_reproduces_every_sealed_decision(result: dict) -> None:
    r = result["modes"]["served_per_item"]
    assert r["n_items"] == result["n_sealed_ids"] == 757
    assert r["n_task_decisions"] == 3028 and r["n_mismatches"] == 0


def test_decisions_do_not_depend_on_the_batch(result: dict) -> None:
    """The property the torch dynamic-int8 variant lacked (166 of 3028 flipped between batch 128 and batch 1)."""
    for mode in ("padded_batches_128", "batch_8"):
        assert result["modes"][mode]["n_mismatches"] == 0, mode


def test_tokens_from_the_served_tokenizer_equal_the_offline_tokenizer(result: dict) -> None:
    tk = result["tokenization"]
    if not tk["checked"]:
        pytest.skip(tk["reason"])
    assert tk["n_different"] == 0
