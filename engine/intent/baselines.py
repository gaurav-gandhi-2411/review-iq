"""Baselines for the review-intent model, scored on the same sealed items and labels (spec Amendment 5).

- majority class of the training labels (no confidence; full coverage only);
- TF-IDF (character n-grams, so Hinglish and typos are not penalised) + logistic regression trained on the SAME
  train split. Its probabilities are uncalibrated; it is run through the same conservative-threshold pipeline and,
  because it has confidences, compared with the model at MATCHED coverage (paired item bootstrap);
- the four labelling judges as plain-prompted LLMs, scored LEAVE-ONE-OUT: judge j's sealed answers are compared with the
  consensus of the OTHER THREE judges (unanimous among them, `consensus_label` needs 3 valid votes), never with a
  consensus that contains j itself, which would be self-labelling. The labelling prompts are task prompts (`ri-judge-v3`)
  run on local 8-9B models, not a Groq 70B arm. Judges return no confidence, so a matched-coverage comparison is not
  possible and none is invented: judges are reported at full coverage (and answered fraction), next to the model at
  full coverage on the SAME items. Unparseable judge answers count as wrong in `accuracy_missing_as_wrong`.
"""

from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from engine.experiments import selective as SEL
from engine.intent import evaluate as E
from engine.labelling.repilot2_report import wilson
from engine.labelling.repilot_report import consensus_label

PANEL = ("llama31_8b", "qwen3_8b", "gemma2_9b", "mistral_7b")
SEED = 42


def majority_baseline(train_y: np.ndarray, classes: list[str], yt: np.ndarray, n_boot: int) -> dict:
    """Predict the most frequent training class for every sealed item."""
    top = int(np.bincount(train_y, minlength=len(classes)).argmax())
    st = E.class_stats(yt, np.full(len(yt), top), classes, n_boot)
    return {"class": classes[top], "coverage": 1.0, **st}


def tfidf_probs(
    train: tuple[list[str], np.ndarray], texts: list[list[str]], n_classes: int
) -> list[np.ndarray] | None:
    """Fit TF-IDF + LR on the train split; class probabilities for each text list. None if <2 classes."""
    tx, ty = train
    if len(set(ty.tolist())) < 2:
        return None
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True)
    lr = LogisticRegression(C=10.0, max_iter=1000, random_state=SEED)
    lr.fit(vec.fit_transform(tx), ty)
    out = []
    for t in texts:
        p = np.zeros((len(t), n_classes))
        p[:, lr.classes_] = lr.predict_proba(vec.transform(t))  # classes absent from train get 0
        out.append(p)
    return out


def matched_coverage(
    correct_a: np.ndarray, conf_a: np.ndarray, correct_b: np.ndarray, conf_b: np.ndarray,
    coverage: float, n_boot: int,
) -> dict:  # fmt: skip
    """Precision of arms A and B at the same answered fraction and their paired difference (A minus B)."""
    pa, ca = SEL.precision_at_coverage(correct_a, conf_a, coverage)
    pb, cb = SEL.precision_at_coverage(correct_b, conf_b, coverage)
    n = len(correct_a)
    idx = np.random.default_rng(SEED).integers(0, n, (n_boot, n))
    d = [
        SEL.precision_at_coverage(correct_a[i], conf_a[i], coverage)[0]
        - SEL.precision_at_coverage(correct_b[i], conf_b[i], coverage)[0]
        for i in idx
    ]
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {
        "target_coverage": E._r(coverage), "precision_a": E._r(pa), "achieved_coverage_a": E._r(ca),
        "precision_b": E._r(pb), "achieved_coverage_b": E._r(cb), "difference_a_minus_b": E._r(pa - pb),
        "difference_ci95": [E._r(lo), E._r(hi)],
    }  # fmt: skip


def judge_value(rec: dict | None, task: str, stratum: str) -> str | None:
    """A judge's answer for a task (core or `aspect:<category>:<aspect>`); None = missing / aspect not quoted."""
    t = (rec or {}).get("text")
    if not t:
        return None
    if task.startswith("aspect:"):
        _, cat, asp = task.split(":")
        return (t.get("aspects") or {}).get(asp) if stratum == cat else None
    return t.get(task)


def llm_leave_one_out(
    task: str, classes: list[str], ids: list[str], pred: np.ndarray,
    items: dict[str, dict], strata: dict[str, str], n_boot: int,
) -> dict:  # fmt: skip
    """Each judge against the consensus of the other three on sealed `ids`; the model is scored on the same items."""
    out: dict = {}
    for j in PANEL:
        others = [o for o in PANEL if o != j]
        keep, gold, ans = [], [], []
        for k, i in enumerate(ids):
            recs = items.get(i, {})
            lab = consensus_label([judge_value(recs.get(o), task, strata[i]) for o in others])
            if lab is None:
                continue
            keep.append(k)
            gold.append(classes.index(lab))
            v = judge_value(recs.get(j), task, strata[i])
            ans.append(classes.index(v) if v in classes else len(classes))  # len = no answer
        if len(keep) < E.MIN_POSITIVES:
            out[j] = {"n_items": len(keep), "insufficient_data": True}
            continue
        g, a, m = np.array(gold), np.array(ans), pred[keep]
        answered = a < len(classes)
        out[j] = {
            "insufficient_data": False, "n_items": len(keep),
            "answered_fraction": E._r(answered.mean()),
            "accuracy_on_answered": E._r((a == g)[answered].mean()) if answered.any() else None,
            "accuracy_on_answered_wilson95": [E._r(x) for x in wilson(int((a == g)[answered].sum()), int(answered.sum()))],
            "judge_missing_as_wrong": E.class_stats(g, a, classes, n_boot),
            "model_on_same_items": E.class_stats(g, m, classes, n_boot),
        }  # fmt: skip
    out["matched_coverage"] = "not available: judges give no confidences; none invented"
    out["scoring"] = "leave-one-out: judge vs unanimous consensus of the other three judges"
    return out


def attach_baselines(
    tasks: dict, labels: dict, preds: dict[str, dict], texts: dict[str, str] | None,
    items: dict[str, dict] | None, strata: dict[str, str], n_boot: int,
) -> None:  # fmt: skip
    """Add `baselines` to every evaluated task in place. `texts` enables TF-IDF, `items` the judge baseline."""
    for task, res in tasks.items():
        if res.get("insufficient_data", True):
            continue
        classes = list(preds[task]["classes"])
        arr = E.task_arrays(labels, preds[task], task)
        (ids_v, yv, _), (ids_t, yt, pt) = arr["val"], arr["test"]
        base: dict = {}
        tr = labels["labels"]["train"][task]
        tr_ids = [i for i in tr if texts is None or i in texts]
        tr_y = np.array([classes.index(tr[i]) for i in tr_ids], dtype=np.int64)
        if len(tr_y):
            base["majority"] = majority_baseline(tr_y, classes, yt, n_boot)
        if texts is not None:
            probs = tfidf_probs(
                ([texts[i] for i in tr_ids], tr_y),
                [[texts[i] for i in ids_v], [texts[i] for i in ids_t]],
                len(classes),
            )
            if probs is None:
                base["tfidf_lr"] = {"insufficient_data": "fewer than 2 training classes"}
            else:
                r = E.evaluate_task(classes, (yv, probs[0]), (yt, probs[1]), n_boot)
                base["tfidf_lr"] = {"calibration": "none (raw LR probabilities)", **r}
                if not r["insufficient_data"]:
                    for tgt in E.TARGETS:
                        cov = res["selective"][f"conservative_p{tgt}"]["coverage"]
                        if cov:  # matched to the model's conservative-threshold coverage on the sealed set
                            base["tfidf_lr"][f"matched_to_model_p{tgt}"] = matched_coverage(
                                pt.argmax(1) == yt,
                                pt.max(1),
                                probs[1].argmax(1) == yt,
                                probs[1].max(1),
                                cov,
                                min(n_boot, 300),
                            )
        if items is not None:
            base["llm_judges_leave_one_out"] = llm_leave_one_out(
                task, classes, ids_t, pt.argmax(1), items, strata, n_boot
            )
        res["baselines"] = base
