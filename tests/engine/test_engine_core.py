from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("scipy")

from engine import data as D  # noqa: E402
from engine import metrics as M  # noqa: E402
from engine import scoring as S  # noqa: E402


def test_holdout_classes_is_pre_registered_and_stable() -> None:
    labels = [f"c{i}" for i in range(150)]
    a = D.holdout_classes(labels, 30)
    assert a == D.holdout_classes(list(reversed(labels)), 30)  # order-independent
    assert len(a) == len(set(a)) == 30


def test_stratified_subsample_covers_every_label_first() -> None:
    rows = [D.Example(f"t{i}", f"l{i % 10}") for i in range(500)]
    sub = D.stratified_subsample(rows, 25)
    assert len(sub) == 25
    assert len({e.label for e in sub}) == 10
    assert sub == D.stratified_subsample(rows, 25)  # deterministic


def test_temperature_recovers_overconfident_logits() -> None:
    rng = np.random.default_rng(0)
    y = rng.integers(0, 5, 2000)
    logits = rng.normal(0, 1, (2000, 5))
    logits[np.arange(2000), y] += 1.0  # weak signal ...
    logits *= 6.0  # ... made overconfident
    assert S.fit_temperature(logits, y) > 2.0


def test_retention_threshold_keeps_95_percent() -> None:
    s = np.linspace(0, 1, 1000)
    thr = S.threshold_for_retention(s, 0.95)
    assert abs((s >= thr).mean() - 0.95) < 0.01


def test_mahalanobis_scores_far_points_lower() -> None:
    rng = np.random.default_rng(1)
    emb = np.r_[rng.normal(0, 1, (300, 8)), rng.normal(5, 1, (300, 8))]
    y = np.r_[np.zeros(300, int), np.ones(300, int)]
    m = S.Mahalanobis(emb, y, 2)
    near = m.score(rng.normal(0, 1, (50, 8)))
    far = m.score(rng.normal(30, 1, (50, 8)))
    assert near.mean() > far.mean()


def test_known_vs_unknown_perfect_separation() -> None:
    r = M.known_vs_unknown(np.linspace(0.9, 1.0, 100), np.linspace(0.0, 0.5, 100))
    assert r["auroc"] == 1.0 and r["fpr_at_95_tpr"] == 0.0


def test_macro_f1_penalises_a_dead_rare_class_that_accuracy_hides() -> None:
    y = np.array([0] * 98 + [1] * 2)
    p = np.zeros(100, dtype=int)  # never predicts the rare class
    assert M.accuracy(y, p) == 0.98
    assert M.macro_f1(y, p) < 0.5


def test_macro_f1_ignores_classes_with_no_support_and_matches_bootstrap_basis() -> None:
    y = np.array([0, 0, 1, 1])
    p = np.array([0, 0, 1, 1])
    # class 2 exists in the label space but not in this evaluation set: it must not count as F1 = 0
    assert M.macro_f1(y, p, [0, 1, 2]) == 1.0
    lo, hi = M.bootstrap_ci(y, p, M.macro_f1, n_boot=50)
    assert lo <= M.macro_f1(y, p, [0, 1, 2]) <= hi


def test_bootstrap_open_set_brackets_the_point_estimate() -> None:
    rng = np.random.default_rng(0)
    known, unknown = rng.normal(2, 1, 400), rng.normal(0, 1, 300)
    thr = float(np.percentile(known, 5))
    ci = M.bootstrap_open_set(known, unknown, thr, n_boot=200)
    point = float((unknown < thr).mean())
    lo, hi = ci["rejection_recall_ci95"]
    assert lo <= point <= hi
    assert 0.5 < ci["auroc_ci95"][0] <= ci["auroc_ci95"][1] <= 1.0


def test_precision_at_coverage_handles_ties_and_perfect_ranking() -> None:
    from engine.experiments import selective as SEL

    correct = np.array([True, True, True, True, False, False, True, False])
    conf = np.array([0.99, 0.95, 0.9, 0.85, 0.5, 0.4, 0.3, 0.2])
    # top 4 are all correct -> coverage 0.5 keeps precision 1.0; coverage at precision 0.95 is 0.5
    assert SEL.coverage_at_precision(correct, conf, 0.95) == 0.5
    p, cov = SEL.precision_at_coverage(correct, conf, 0.5)
    assert (p, cov) == (1.0, 0.5)
    # a tie group is admitted whole: three items share 0.5, so coverage 0.5 -> 0.75 jumps over it
    conf_tied = np.array([0.9, 0.9, 0.5, 0.5, 0.5, 0.1, 0.1, 0.1])
    _, cov_t = SEL.precision_at_coverage(correct, conf_tied, 0.4)
    assert cov_t == 0.625
    # an arm that is never right reaches no precision target
    assert SEL.coverage_at_precision(np.zeros(5, dtype=bool), np.arange(5.0), 0.95) == 0.0


def test_selective_summary_ci_brackets_the_point_estimate() -> None:
    from engine.experiments import selective as SEL

    rng = np.random.default_rng(1)
    conf = rng.random(600)
    correct = rng.random(600) < (0.5 + 0.5 * conf)  # confidence is informative
    s = SEL.summarize(correct, conf, n_boot=200)
    lo, hi = s["precision_at_coverage_0.8"]["ci95"]
    assert lo <= s["precision_at_coverage_0.8"]["precision"] <= hi
    assert (
        s["precision_at_coverage_0.7"]["precision"] >= s["precision_at_coverage_0.9"]["precision"]
    )


def test_threshold_transfer_is_conservative_for_an_informative_score() -> None:
    from engine.experiments import selective as SEL

    rng = np.random.default_rng(3)
    conf = rng.random(2000)
    correct = rng.random(2000) < (0.55 + 0.45 * conf)  # precision rises with confidence
    tr = SEL.threshold_transfer(correct, conf, n_splits=60)
    # an oracle threshold picks the best cut on the same items; a transferred one cannot beat it by luck
    assert tr["coverage_mean"] <= SEL.coverage_at_precision(correct, conf, 0.95) + 0.05
    assert 0.0 <= tr["hit_rate"] <= 1.0
    # a score that carries no information never reaches the target except by answering almost nothing
    tr0 = SEL.threshold_transfer(rng.random(2000) < 0.5, rng.random(2000), n_splits=60)
    assert tr0["coverage_mean"] < 0.2
