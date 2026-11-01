from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")  # absent from the CI venv: skip, as test_engine_core does

from engine.intent import baselines as B  # noqa: E402
from engine.intent import evaluate as E  # noqa: E402

CL = ["yes", "no"]


def jrec(v: str | None, aspects: dict | None = None) -> dict:
    if v is None:
        return {"text": None}
    return {"text": {"needs_action": v, "aspects": aspects or {}}}


def test_judge_value_core_aspect_and_missing() -> None:
    r = jrec("yes", {"taste": "positive"})
    assert B.judge_value(r, "needs_action", "food") == "yes"
    assert B.judge_value(r, "aspect:food:taste", "food") == "positive"
    assert B.judge_value(r, "aspect:beauty:taste", "food") is None  # other category's task
    assert B.judge_value(r, "aspect:food:value", "food") is None  # not quoted
    assert B.judge_value(jrec(None), "needs_action", "food") is None
    assert B.judge_value(None, "needs_action", "food") is None


def test_leave_one_out_never_scores_a_judge_against_a_consensus_it_voted_in() -> None:
    ids = [f"t{i}" for i in range(30)]
    items = {}
    for k, i in enumerate(ids):
        # llama dissents on the first 8 items; the other three are always unanimous 'yes'
        items[i] = {
            "llama31_8b": jrec("no" if k < 8 else "yes"),
            "qwen3_8b": jrec("yes"),
            "gemma2_9b": jrec("yes"),
            "mistral_7b": jrec(None if k == 29 else "yes"),
        }
    strata = dict.fromkeys(ids, "food")
    model_pred = np.zeros(30, dtype=int)  # model says 'yes' everywhere
    r = B.llm_leave_one_out("needs_action", CL, ids, model_pred, items, strata, 50)
    ll = r["llama31_8b"]
    assert (
        ll["n_items"] == 29
    )  # item 29: mistral missing -> 'others' lack 3 valid votes -> excluded
    assert ll["accuracy_on_answered"] == pytest.approx(21 / 29, abs=1e-3)
    # qwen is judged against {llama, gemma, mistral}: llama's dissent breaks unanimity, so those 8 items drop out
    assert r["qwen3_8b"]["n_items"] == 21 and r["qwen3_8b"]["accuracy_on_answered"] == 1.0
    assert "model_on_same_items" in ll and ll["model_on_same_items"]["n"] == 29
    assert "no confidences" in r["matched_coverage"] or "not available" in r["matched_coverage"]


def test_missing_judge_answers_count_as_wrong_not_dropped() -> None:
    ids = [f"t{i}" for i in range(25)]
    items = {
        i: {
            "llama31_8b": jrec(None if k < 5 else "yes"),
            "qwen3_8b": jrec("yes"),
            "gemma2_9b": jrec("yes"),
            "mistral_7b": jrec("yes"),
        }
        for k, i in enumerate(ids)
    }
    r = B.llm_leave_one_out(
        "needs_action", CL, ids, np.zeros(25, int), items, dict.fromkeys(ids, "food"), 30
    )
    ll = r["llama31_8b"]
    assert ll["answered_fraction"] == 0.8 and ll["accuracy_on_answered"] == 1.0
    assert ll["judge_missing_as_wrong"]["accuracy"] == 0.8


def test_tfidf_learns_and_pads_absent_classes() -> None:
    tx = ["broken item arrived", "totally broken", "love it", "lovely and great"] * 10
    ty = np.array([0, 0, 1, 1] * 10)
    p = B.tfidf_probs((tx, ty), [["so broken", "great love"]], 3)[0]
    assert p.shape == (2, 3) and p[:, 2].sum() == 0 and p[0].argmax() == 0 and p[1].argmax() == 1
    assert B.tfidf_probs((tx[:2], np.array([0, 0])), [["x"]], 2) is None


def test_matched_coverage_prefers_the_informative_confidence() -> None:
    rng = np.random.default_rng(0)
    correct = rng.random(400) < 0.8
    informative = correct + rng.random(400) * 0.5  # ranks correct answers first
    noise = rng.random(400)
    r = B.matched_coverage(correct, informative, correct, noise, 0.5, 100)
    assert r["precision_a"] > r["precision_b"] and r["difference_ci95"][0] > 0
    assert abs(r["achieved_coverage_a"] - 0.5) < 0.02


def test_majority_baseline_predicts_the_training_mode() -> None:
    yt = np.array([0] * 30 + [1] * 70)
    r = B.majority_baseline(np.array([1, 1, 1, 0]), CL, yt, 50)
    assert r["class"] == "no" and r["accuracy"] == 0.7
    assert r["classes"]["yes"]["f1"] == 0.0


def test_attach_baselines_end_to_end() -> None:
    rng = np.random.default_rng(5)
    words = {"yes": "broken awful", "no": "lovely great"}
    texts, lab = {}, {s: {"needs_action": {}} for s in ("train", "val", "test")}
    ids = {"train": 120, "val": 120, "test": 120}
    pred_splits, items = {}, {}
    for s, n in ids.items():
        sid, probs = [], []
        for k in range(n):
            i = f"{s}{k}"
            g = "yes" if rng.random() < 0.5 else "no"
            texts[i] = f"{words[g]} {k % 7}"
            lab[s]["needs_action"][i] = g
            sid.append(i)
            ok = rng.random() < 0.9
            c = rng.uniform(0.6, 0.99) if ok else rng.uniform(0.5, 0.6)
            gi = CL.index(g)
            p = [0.0, 0.0]
            p[gi if ok else 1 - gi], p[1 - gi if ok else gi] = c, 1 - c
            probs.append(p)
            if s == "test":
                items[i] = dict.fromkeys(B.PANEL, jrec(g))
        pred_splits[s] = {"ids": sid, "probs": probs}
    preds = {"needs_action": {"task": "needs_action", "classes": CL, "splits": pred_splits}}
    labels = {"classes": {"needs_action": CL}, "labels": lab}
    strata = dict.fromkeys(texts, "food")
    tasks = E.evaluate_all(labels, preds, strata, 100)
    B.attach_baselines(tasks, labels, preds, texts, items, strata, 100)
    base = tasks["needs_action"]["baselines"]
    assert base["majority"]["class"] in CL
    assert base["tfidf_lr"]["accuracy"] > 0.9 and "selective" in base["tfidf_lr"]
    assert base["llm_judges_leave_one_out"]["llama31_8b"]["accuracy_on_answered"] == 1.0
