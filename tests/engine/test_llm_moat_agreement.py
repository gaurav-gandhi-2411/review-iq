from __future__ import annotations

import numpy as np
from engine.intent import llm_moat as M
from engine.intent import llm_moat_agreement as A


def _row(i, label, conf):
    return {"id": i, "ans": {t: {"label": label[t], "confidence": conf} for t in M.TASKS}}


def test_kappa_and_spearman_known_values():
    a = np.array(["x", "x", "y", "y"])
    assert A.kappa(a, a) == 1.0
    assert A.kappa(a, np.array(["y", "y", "x", "x"])) == -1.0
    assert abs(A.spearman(np.array([1, 2, 3.0]), np.array([10, 20, 30.0])) - 1.0) < 1e-9
    assert np.isnan(A.spearman(np.array([1, 1, 1.0]), np.array([1, 2, 3.0])))


def test_analyse_overlap_and_unparseable():
    lab = {
        "needs_action": "yes",
        "broad_intent": "praise",
        "sentiment": "positive",
        "urgency": "low",
    }
    flip = dict(lab, needs_action="no")
    groq = {f"i{k}": _row(f"i{k}", lab, 80 + k) for k in range(20)}
    local = {f"i{k}": _row(f"i{k}", flip if k < 5 else lab, 80 + k) for k in range(20)}
    local["i19"] = {"id": "i19", "ans": None}
    local["extra"] = _row("extra", lab, 50)
    gold = {t: {f"i{k}": lab[t] for k in range(20)} for t in M.TASKS}
    res = A.analyse(groq, local, gold)
    assert res["n_overlap"] == 20
    na = res["tasks"]["needs_action"]
    assert na["n_pairs_both_parsed"] == 19 and na["n_unparseable_local"] == 1
    assert abs(na["label_agreement"] - 14 / 19) < 1e-3
    assert res["tasks"]["sentiment"]["label_agreement"] == 1.0
    assert abs(na["spearman_confidence"] - 1.0) < 1e-3
