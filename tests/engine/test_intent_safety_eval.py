from __future__ import annotations

import numpy as np
import pytest
from engine.intent.safety_eval import aggregates, evaluate, f1_threshold, load_pred

CL = ["low", "medium", "high"]


def pred(p_high: list[float], ids: list[str], vp: list[float], vids: list[str]) -> dict:
    def probs(ph: list[float]) -> list[list[float]]:
        return [[(1 - p) / 2, (1 - p) / 2, p] for p in ph]

    return {
        "classes": CL,
        "splits": {
            "test": {"ids": ids, "probs": probs(p_high)},
            "val": {"ids": vids, "probs": probs(vp)},
        },
    }


def test_a_near_duplicate_group_counts_once_for_recall() -> None:
    # 3 near-duplicate positives (one group) all flagged + 1 independent positive missed -> recall 1/2, not 3/4
    y = np.array([True, True, True, True, False])
    flag = np.array([True, True, True, False, False])
    g = np.array([0, 0, 0, 1, 2])
    a = aggregates(y, flag, g)
    assert a["has_pos"].sum() == 2 and (a["hit"] * a["has_pos"]).sum() == 1.0


def test_precision_weights_items_by_group_size() -> None:
    y = np.array([True, True, True, False])
    flag = np.array([True, True, True, True])
    g = np.array(
        [0, 0, 0, 1]
    )  # three duplicate true positives = one unit of true positive; one independent false positive
    a = aggregates(y, flag, g)
    assert a["tp_w"].sum() / a["fl_w"].sum() == pytest.approx(0.5)


def test_f1_threshold_picks_best_and_breaks_ties_upward() -> None:
    p = np.array([0.9, 0.8, 0.3, 0.2])
    y = np.array([True, True, False, False])
    assert f1_threshold(p, y) == pytest.approx(0.8)  # 0.9 -> F1 .67, 0.8 -> 1.0
    assert f1_threshold(np.array([0.9, 0.9]), np.array([True, True])) == pytest.approx(0.9)


def test_evaluate_drops_excluded_and_abstained_and_pairs_runs() -> None:
    ids = [f"t{i}" for i in range(8)]
    urgency = {i: ("high" if j < 4 else "low") for j, i in enumerate(ids)} | {
        "v0": "high",
        "v1": "low",
        "v2": "high",
        "v3": "low",
    }
    urgency["t7"] = None  # abstained: dropped
    shas = {i: f"h{i}" for i in urgency}
    groups = {i: j for j, i in enumerate(ids)}
    vids = ["v0", "v1", "v2", "v3"]
    good = pred([0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1], ids, [0.9, 0.1, 0.8, 0.2], vids)
    weak = pred([0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1], ids, [0.9, 0.1, 0.4, 0.2], vids)
    res = evaluate(
        {"run2": load_pred_obj(weak), "run3": load_pred_obj(good)},
        "run2",
        urgency,
        shas,
        groups,
        {"ht6"},
        n_boot=200,
    )
    assert (
        res["n_test_items_evaluated"] == 6 and res["n_ledger_excluded_dropped"] == 1
    )  # t6 excluded, t7 abstained
    assert res["independent_positive_groups"] == 4
    assert (
        res["runs"]["run3"]["P_argmax"]["recall"] == 1.0
        and res["runs"]["run2"]["P_argmax"]["recall"] == 0.25
    )
    d = res["paired_vs_baseline"]["run3"]["P_argmax"]
    assert d["recall_diff"] == 0.75 and d["recall_diff_group_boot95"][0] > 0


def load_pred_obj(d: dict) -> dict:
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "p.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        return load_pred(p)


def _base(diffs: dict[str, float]) -> dict:
    return {
        t: {"runs": {"run3": {"macro_f1": 0.9, "diff_vs_baseline": d}}} for t, d in diffs.items()
    }


def _safety(rec_lo: float, prec_diff: float) -> dict:
    p = {"recall_diff": 0.5, "recall_diff_group_boot95": [rec_lo, 0.9], "precision_diff": prec_diff}
    return {"paired_vs_baseline": {"run3": {"P_argmax": p}}}


def test_verdict_requires_both_criteria_as_registered() -> None:
    from engine.intent.run3_compare import verdict

    ok = {"needs_action": -0.01, "broad_intent": 0.0, "sentiment": 0.01, "urgency": -0.019}
    assert verdict(_base(ok), _safety(0.1, -0.04), "run2", "run3", None)["outcome"] == "PROMOTED"
    bad_a = ok | {"urgency": -0.03}  # a core task degrades by more than 0.02
    assert verdict(_base(bad_a), _safety(0.1, 0.0), "run2", "run3", None)["outcome"].startswith(
        "NEGATIVE"
    )
    # recall interval touching 0, or precision down by more than 0.05, is not a safety gain
    assert verdict(_base(ok), _safety(0.0, 0.0), "run2", "run3", None)["outcome"].startswith(
        "NEGATIVE"
    )
    assert verdict(_base(ok), _safety(0.1, -0.17), "run2", "run3", None)["outcome"].startswith(
        "NEGATIVE"
    )
