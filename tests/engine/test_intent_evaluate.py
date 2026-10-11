from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")  # absent from the CI venv: skip, as test_engine_core does

from engine.experiments import selective as SEL  # noqa: E402
from engine.intent import evaluate as E  # noqa: E402
from sklearn.metrics import f1_score  # noqa: E402

from engine import metrics as M  # noqa: E402

CL = ["yes", "no"]


def probs_for(y: np.ndarray, rng: np.random.Generator, acc: float = 0.85) -> np.ndarray:
    """Calibrated-ish 2-class probabilities: confidence tracks correctness."""
    right = rng.random(len(y)) < acc
    conf = np.where(right, rng.uniform(0.7, 0.99, len(y)), rng.uniform(0.5, 0.7, len(y)))
    pred = np.where(right, y, 1 - y)
    p = np.zeros((len(y), 2))
    p[np.arange(len(y)), pred] = conf
    p[np.arange(len(y)), 1 - pred] = 1 - conf
    return p


def test_val_threshold_is_the_lowest_confidence_that_clears_the_wilson_bound() -> None:
    rng = np.random.default_rng(0)
    conf = rng.random(300).round(2)
    correct = rng.random(300) < (0.5 + 0.5 * conf)
    thr = E.val_threshold(correct, conf, 0.9)
    assert math.isfinite(thr)

    def lb(t: float) -> float:
        k = conf >= t
        return float(
            SEL.wilson_lower(np.array([correct[k].sum()], float), np.array([k.sum()], float))[0]
        )

    assert lb(thr) >= 0.9
    lower = np.unique(conf[conf < thr])
    assert all(lb(t) < 0.9 for t in lower)  # no lower threshold clears the bound
    # too few items to ever reach the bound -> answer nothing, never a lucky threshold
    assert E.val_threshold(np.ones(20, bool), np.linspace(0.6, 0.9, 20), 0.95) == math.inf


def test_f1_matrix_matches_sklearn_and_macro_f1() -> None:
    rng = np.random.default_rng(1)
    y, p = rng.integers(0, 3, 200), rng.integers(0, 3, 200)
    f = E._f1_matrix(y, p, 3)
    assert np.allclose(f, f1_score(y, p, labels=[0, 1, 2], average=None))
    assert np.isclose(np.nanmean(f), M.macro_f1(y, p, [0, 1, 2]))
    assert (
        E._f1_matrix(np.zeros(5, int), np.zeros(5, int), 2)[1]
        != E._f1_matrix(np.zeros(5, int), np.zeros(5, int), 2)[1]
    )  # NaN: absent class  # noqa: E501


def test_class_stats_flags_insufficient_classes_and_floor() -> None:
    y = np.array([0] * 60 + [1] * 25 + [2] * 5)
    pred = y.copy()
    pred[60:70] = 0  # class 1 recall 15/25
    st = E.class_stats(y, pred, ["a", "b", "c"], 200)
    assert st["classes"]["c"] == {"n": 5, "insufficient_data": True}  # never a metric
    assert st["classes"]["b"]["low_support_lt30"] and st["classes"]["b"]["f1"] < 0.8
    assert st["macro_f1_classes"] == ["a", "b"]
    lo, hi = st["macro_f1_ci95"]
    assert lo <= st["macro_f1"] <= hi
    one = E.class_stats(np.array([0] * 40 + [1] * 3), np.array([0] * 43), ["a", "b"], 50)
    assert isinstance(one["macro_f1"], str) and "insufficient" in one["macro_f1"]


def test_evaluate_task_conservative_vs_oracle_and_threshold_comes_from_validation() -> None:
    rng = np.random.default_rng(2)
    yv, yt = rng.integers(0, 2, 600), rng.integers(0, 2, 600)
    pv, pt = probs_for(yv, rng), probs_for(yt, rng)
    r = E.evaluate_task(CL, (yv, pv), (yt, pt), 200)
    c = r["selective"]["conservative_p0.95"]
    assert c["threshold"] is not None and 0 < c["coverage"] <= 1
    assert c["precision"] == pytest.approx(
        (pt.argmax(1) == yt)[pt.max(1) >= c["threshold"]].mean(), abs=1e-4
    )
    assert "ORACLE" in r["selective"]["oracle"]["label"]
    # changing the SEALED labels must not move the validation-chosen threshold
    r2 = E.evaluate_task(CL, (yv, pv), (1 - yt, pt), 200)
    assert r2["selective"]["conservative_p0.95"]["threshold"] == c["threshold"]
    # a validation set too small to certify precision -> answer nothing
    r3 = E.evaluate_task(CL, (yv[:10], pv[:10]), (yt, pt), 50)
    assert r3["selective"]["conservative_p0.95"]["coverage"] == 0.0
    assert r3["selective"]["conservative_p0.95"]["precision"] is None
    assert E.evaluate_task(CL, (yv, pv), (yt[:5], pt[:5]))["insufficient_data"] is True


def synthetic_run(tmp: Path) -> tuple[Path, Path, Path]:
    rng = np.random.default_rng(3)
    ids = {"val": [f"v{i}" for i in range(300)], "test": [f"t{i}" for i in range(300)]}
    gold = {s: {i: ("yes" if rng.random() < 0.5 else "no") for i in v} for s, v in ids.items()}
    labels = {
        "classes": {"needs_action": CL, "urgency": ["low", "high"]},
        "labels": {"train": {"needs_action": {}, "urgency": {}},
                   "val": {"needs_action": gold["val"], "urgency": {}},
                   "test": {"needs_action": {i: g for i, g in gold["test"].items() if i != "t0"}, "urgency": {}}},
    }  # fmt: skip
    pred_dir = tmp / "run"
    pred_dir.mkdir()
    sp = {}
    for s in ("val", "test"):
        y = np.array([CL.index(gold[s][i]) for i in ids[s]])
        sp[s] = {"ids": ids[s], "probs": probs_for(y, rng).round(6).tolist()}
    (pred_dir / "pred_needs_action.json").write_text(
        json.dumps({"task": "needs_action", "classes": CL, "temperature": 1.0, "splits": sp})
    )
    (pred_dir / "metadata.json").write_text(json.dumps({"commit": "abc1234"}))
    man = {"items": [{"id": i, "stratum": "food" if int(i[1:]) % 2 else "vernacular", "split": s, "sha": f"sha-{i}"}
                     for s, v in ids.items() for i in v]}  # fmt: skip
    (tmp / "labels.json").write_text(json.dumps(labels))
    (tmp / "manifest.json").write_text(json.dumps(man))
    return tmp / "labels.json", tmp / "manifest.json", pred_dir


def test_main_end_to_end_flags_missing_task_ceiling_slices_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab, man, pred_dir = synthetic_run(tmp_path)
    out = tmp_path / "eval.json"
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"sealed_excluded": ["sha-t5"]})
    )  # t5 is labelled: it must vanish from the sealed set
    monkeypatch.setattr(
        "sys.argv",
        ["x", "--labels", str(lab), "--manifest", str(man), "--pred-dir", str(pred_dir),
         "--out", str(out), "--n-boot", "100", "--ledger", str(ledger)],
    )  # fmt: skip
    E.main()
    rep = json.loads(out.read_text())
    assert rep["tasks"]["urgency"] == {"missing_predictions": True}
    t = rep["tasks"]["needs_action"]
    assert t["consensus_ceiling"]["n_labelled"] == 298 and t["consensus_ceiling"]["n_sealed"] == 299
    assert rep["provenance"]["ledger_sealed_excluded"]["n_sealed_items_dropped"] == 1
    assert t["slices_accuracy"]["food"]["claimed"] and "accuracy" in t["slices_accuracy"]["food"]
    assert rep["provenance"]["training_metadata"]["commit"] == "abc1234"
    assert set(rep["provenance"]["artifacts_sha256_16"]) >= {
        "labels.json",
        "pred_needs_action.json",
    }
    assert "silver" in rep["provenance"]["label_basis"]


def test_drop_excluded_removes_only_ledger_hashes_from_every_task() -> None:
    manifest = {"items": [{"id": f"t{i}", "split": "test", "sha": f"h{i}"} for i in range(4)]
                + [{"id": "v0", "split": "val", "sha": "h0"}]}  # fmt: skip
    mk = lambda: {"classes": ["a", "b"], "splits": {"test": {"ids": ["t0", "t1", "t2", "t3"],  # noqa: E731
                  "probs": [[0.6, 0.4], [0.9, 0.1], [0.2, 0.8], [0.5, 0.5]]}}}  # fmt: skip
    preds = {"x": mk(), "y": mk()}
    assert E.drop_excluded(preds, manifest, {"h1", "h3", "nope"}) == 2
    for d in preds.values():
        assert d["splits"]["test"]["ids"] == ["t0", "t2"]
        assert d["splits"]["test"]["probs"] == [[0.6, 0.4], [0.2, 0.8]]


def test_cluster_ci_is_wider_when_errors_cluster_and_equals_item_level_for_singletons() -> None:
    rng = np.random.default_rng(1)
    n, per = 600, 20
    cl_id = np.repeat(np.arange(n // per), per)  # 30 products x 20 reviews
    bad = rng.random(n // per) < 0.3  # whole products are wrong together
    y = np.zeros(n, dtype=np.int64)
    pred = np.where(bad[cl_id], 1, 0)
    conf = np.full(n, 0.9)
    clustered = E.cluster_cis(y, pred, conf, {}, CL, cl_id.astype(str), 400)
    solo = E.cluster_cis(y, pred, conf, {}, CL, np.array([f"solo:{i}" for i in range(n)]), 400)
    w = lambda ci: ci[1] - ci[0]  # noqa: E731
    assert w(clustered["accuracy_cluster_ci95"]) > 2 * w(solo["accuracy_cluster_ci95"])
    acc = (y == pred).mean()
    item = E.wilson(int((y == pred).sum()), n)
    assert abs(w(solo["accuracy_cluster_ci95"]) - (item[1] - item[0])) < 0.01
    assert solo["accuracy_cluster_ci95"][0] < acc < solo["accuracy_cluster_ci95"][1]
    assert clustered["n_clusters"] == 30 and solo["n_clusters"] == n
