"""Served code path vs the offline evaluation on the SEALED set (S24 A6). Skips cleanly without the artifacts or texts.

Needs: torch + transformers + scikit-learn, the model artifacts (CLASSIFY_PARITY_ARTIFACTS, default
D:/ml-cache/s24/classify_artifacts) and the private review texts (CLASSIFY_PARITY_TEXTS, default
D:/ml-cache/review-intent/scale.jsonl). Review text is never written. Runs the same checks as scripts/classify_parity.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("sklearn")
pytest.importorskip("torch")
pytest.importorskip("transformers")

from scripts import classify_parity as P  # noqa: E402

ARTIFACTS = Path(os.environ.get("CLASSIFY_PARITY_ARTIFACTS", "D:/ml-cache/s24/classify_artifacts"))
TEXTS = Path(os.environ.get("CLASSIFY_PARITY_TEXTS", "D:/ml-cache/review-intent/scale.jsonl"))


def test_thresholds_file_equals_offline_recomputation_bit_for_bit() -> None:
    assert P.thresholds_bit_for_bit()["equal"] is True


@pytest.fixture(scope="module")
def result() -> dict:
    if not (ARTIFACTS / "model_int8.pt").is_file() or not TEXTS.is_file():
        pytest.skip("model artifacts or the private review texts are not on this machine")
    return P.run(ARTIFACTS, TEXTS)


def test_same_code_path_reproduces_offline_decisions_exactly(result: dict) -> None:
    """Offline batching (128, pred-file order): every sealed item and task identical, probabilities identical."""
    r = result["modes"]["offline_batches"]
    assert r["n_items"] == result["n_sealed_ids"] == 757
    assert r["n_mismatches"] == 0 and r["max_abs_prob_diff"] == 0.0


def test_served_per_item_differs_only_by_abstention_never_by_class(result: dict) -> None:
    """Per-tensor dynamic int8 makes logits depend on the batch, so per-item serving is not bit-identical to the batch-128
    evaluation; where both answer, they agree on the class (observed: 0 of 3028 decisions differ in class)."""
    r = result["modes"]["served_per_item"]
    assert r["mismatch_kinds"]["different_class"] == 0
