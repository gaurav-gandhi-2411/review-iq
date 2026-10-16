from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pytest
from engine.intent import decide as D
from engine.intent import evaluate as EV

CLASSES = ["low", "medium", "high"]
HIGH = 2


def _val(n_pos: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Validation: rare positives only reach p(high) ~ 0.30 (a scale shift argmax cannot see); negatives sit near 0.02."""
    rng = np.random.default_rng(0)
    y = np.array([HIGH] * n_pos + [0] * 40 + [1] * 12)
    p = np.zeros((len(y), 3))
    p[:n_pos] = [0.55, 0.15, 0.30]
    p[n_pos : n_pos + 40] = [0.93, 0.05, 0.02]
    p[n_pos + 40 :] = [0.10, 0.88, 0.02]
    p += rng.normal(0, 1e-3, p.shape)
    return y, p / p.sum(1, keepdims=True)


def test_default_mode_is_threshold_everywhere() -> None:
    assert D.DEFAULT_MODE == "threshold"
    assert inspect.signature(D.decide).parameters["mode"].default == "threshold"
    assert inspect.signature(EV.evaluate_all).parameters["mode"].default == "threshold"
    assert inspect.signature(EV.evaluate_task).parameters["mode"].default == "threshold"
    assert D.rare_for("urgency") == ("high",) and D.rare_for("sentiment") == ()


def test_default_does_not_use_argmax_for_a_rare_class() -> None:
    y, p = _val()
    thr = D.fit_thresholds(y, p, CLASSES, ("high",))
    t = thr["high"]["threshold"]
    assert 0.2 < t < 0.35  # fitted to the validation positives' probability scale, not 0.5
    # (a) a rare class is NOT predicted just because it is the argmax when the threshold says no
    strict = {"high": {"threshold": 0.9, "n_val_positives": 8, "fallback_argmax": False}}
    only = np.array([[0.20, 0.10, 0.70]])
    assert D.decide(only, CLASSES, strict, mode="argmax")[0] == HIGH
    assert D.decide(only, CLASSES, strict)[0] == 0  # default: non-rare argmax


def test_threshold_catches_a_high_that_argmax_misses() -> None:
    y, p = _val()
    thr = D.fit_thresholds(y, p, CLASSES, ("high",))
    miss = np.array(
        [[0.58, 0.10, 0.32], [0.93, 0.05, 0.02]]
    )  # item 0: a 'high' at the validated scale
    assert D.decide(miss, CLASSES, thr, mode="argmax").tolist() == [0, 0]  # argmax misses it
    assert D.decide(miss, CLASSES, thr).tolist() == [
        HIGH,
        0,
    ]  # default catches it, item 1 untouched


def test_thresholds_come_from_validation_only() -> None:
    y, p = _val()
    a = D.fit_thresholds(y, p, CLASSES, ("high",))
    # the fit function has no test input at all
    assert list(inspect.signature(D.fit_thresholds).parameters) == [
        "y_val",
        "p_val",
        "classes",
        "rare",
    ]
    # evaluate_task: changing the sealed (test) set must not move the threshold; changing validation must
    yt = np.array([HIGH] * 25 + [0] * 25)
    pt1 = np.tile([0.6, 0.1, 0.3], (50, 1))
    pt2 = np.tile([0.2, 0.1, 0.7], (50, 1))
    r1 = EV.evaluate_task(CLASSES, (y, p), (yt, pt1), 20, rare=("high",))
    r2 = EV.evaluate_task(CLASSES, (y, p), (yt, pt2), 20, rare=("high",))
    assert (
        r1["decision"]["thresholds_fitted_on_validation"]
        == r2["decision"]["thresholds_fitted_on_validation"]
        == a
    )
    p_shift = p.copy()
    p_shift[:, [0, HIGH]] = p_shift[:, [HIGH, 0]]
    r3 = EV.evaluate_task(CLASSES, (y, p_shift), (yt, pt1), 20, rare=("high",))
    assert r3["decision"]["thresholds_fitted_on_validation"] != a


def test_too_few_validation_positives_falls_back_to_argmax_and_says_so() -> None:
    y, p = _val(n_pos=D.MIN_VAL_POSITIVES - 1)
    thr = D.fit_thresholds(y, p, CLASSES, ("high",))
    assert thr["high"]["fallback_argmax"] is True and thr["high"]["threshold"] is None
    row = np.array([[0.58, 0.10, 0.32]])
    assert D.decide(row, CLASSES, thr)[0] == D.decide(row, CLASSES, thr, mode="argmax")[0] == 0


def test_unknown_mode_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown decision mode"):
        D.decide(np.array([[0.5, 0.3, 0.2]]), CLASSES, {}, mode="argmx")


def test_write_predictions_refits_thresholds_per_variant_on_validation(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("scipy")
    from engine.intent import train as T

    y, p = _val()
    ids_v, ids_t = [f"v{i}" for i in range(len(y))], ["t0", "t1"]
    arr = {"val": (ids_v, {"urgency": y}), "test": (ids_t, {"urgency": np.array([0, HIGH])})}
    logits_v = np.log(p)
    found = {}
    noise = np.random.default_rng(1).normal(
        0, 0.6, logits_v.shape
    )  # int8-like perturbation (temperature cannot undo it)
    for name, shift in (("fp32", 0.0), ("int8", 1.0)):
        out = tmp_path / name
        out.mkdir()
        test_lg = np.log(np.array([[0.9, 0.05, 0.05], [0.5, 0.2, 0.3]]))
        lg = {"val": {"urgency": logits_v + shift * noise}, "test": {"urgency": test_lg}}
        dec: dict = {}
        T.write_predictions(out, {"urgency": tuple(CLASSES)}, arr, lg, dec)
        pred = json.loads((out / "pred_urgency.json").read_text(encoding="utf-8"))
        assert pred["decision"]["mode"] == "threshold"
        assert pred["decision"]["thresholds"] == dec["urgency"]
        found[name] = dec["urgency"]["high"]["threshold"]
    assert found["fp32"] != found["int8"]  # refit per variant, not copied from fp32
