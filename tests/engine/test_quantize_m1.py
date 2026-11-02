"""Amendment 16 automatic layer-sensitivity rule (serving spec Addendum 1) and its helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip(
    "onnxruntime"
)  # the module imports numpy only at top level, but the CI venv skips the serving extras together
from engine.intent import quantize_m1 as Q  # noqa: E402


def test_layers_below_the_threshold_keep_their_second_ffn_matmul_in_fp32() -> None:
    # S25 (laptop numerics): layers 2 to 8 failed the 0.995 rule; this reproduces its bookkeeping, not its numbers
    agreement = {
        0: 1.0,
        1: 0.9996,
        2: 0.99,
        3: 0.97,
        4: 0.994999,
        5: 0.995,
        6: 0.5,
        7: 0.9951,
        8: 0.2,
        9: 1.0,
        10: 0.9999,
        11: 0.995,
    }
    safe = Q.choose_safe_layers(agreement)
    assert safe == (0, 1, 5, 7, 9, 10, 11)  # >= 0.995 is safe, 0.994999 is not
    assert Q.m1_skip_layers(safe) == (2, 3, 4, 6, 8)


def test_the_acceptance_and_safe_thresholds_are_the_registered_ones() -> None:
    assert (Q.SAFE_AGREEMENT, Q.ACCEPT_AGREEMENT, Q.N_CALIB, Q.SEED, Q.N_LAYERS) == (
        0.995,
        0.90,
        256,
        42,
        12,
    )


def test_mean_agreement_averages_argmax_agreement_over_the_four_served_tasks() -> None:
    ref = {t: np.array([0, 1, 0, 1]) for t in Q.SERVED}
    lg = {t: np.array([[1.0, 0], [0, 1.0], [1.0, 0], [0, 1.0]]) for t in Q.SERVED}
    assert Q.mean_agreement(lg, ref) == 1.0
    lg["urgency"] = np.array([[0, 1.0], [0, 1.0], [1.0, 0], [0, 1.0]])  # 3 of 4 agree on one task
    assert Q.mean_agreement(lg, ref) == pytest.approx((3 + 0.75) / 4)


def test_split_logits_follows_the_head_column_order() -> None:
    lg = np.arange(12, dtype=float).reshape(1, 12)
    out = Q.split_logits(lg, {"needs_action": 2, "broad_intent": 5, "sentiment": 4})
    assert (
        out["needs_action"].tolist() == [[0, 1]]
        and out["broad_intent"].shape == (1, 5)
        and out["sentiment"].tolist() == [[7, 8, 9, 10]]
    )


def test_the_fp32_reference_is_aligned_to_the_requested_ids(tmp_path: Path) -> None:
    ids = ["a", "b", "c"]
    for t in Q.SERVED:
        probs = [[0.9, 0.1], [0.2, 0.8], [0.6, 0.4]]
        (tmp_path / f"pred_{t}.json").write_text(
            json.dumps({"classes": ["x", "y"], "splits": {"val": {"ids": ids, "probs": probs}}}),
            encoding="utf-8",
        )
    ref = Q.fp32_ref_argmax(tmp_path, ["c", "a"])  # a subset, in a different order
    assert ref["needs_action"].tolist() == [0, 0]
    assert Q.fp32_ref_argmax(tmp_path, ["b"])["urgency"].tolist() == [1]
