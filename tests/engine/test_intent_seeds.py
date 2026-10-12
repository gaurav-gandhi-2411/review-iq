from __future__ import annotations

import math

import pytest

pytest.importorskip("scipy")
from engine.intent import seeds as S  # noqa: E402
from engine.intent.run3_compare import CORE  # noqa: E402


def test_interval_matches_the_t_formula_by_hand() -> None:
    r = S.interval([1.0, 2.0, 3.0, 4.0, 5.0])
    half = 2.7764451 * math.sqrt(2.5) / math.sqrt(5)  # t(0.975, df=4) * sd / sqrt(n)
    assert r["mean"] == 3.0 and r["sd"] == round(math.sqrt(2.5), 4)
    assert r["t95"] == [round(3.0 - half, 4), round(3.0 + half, 4)]
    assert S.interval([1.0])["t95"] is None and S.interval([None, 2.0])["n_seeds"] == 1


def _run(arm: str, seed: int, rec: float, prec: float, macro: float) -> dict:
    a = {t: macro for t in CORE}
    meta = {"train_commit": "abc", "device": "cuda", "torch": "2.6", "best_epoch": 9, "stopped_early": True, "best_val_mean_macro_f1": 0.7, "train_seconds": 300}  # fmt: skip
    return meta | {"arm": arm, "seed": seed, "variants": {S.HEADLINE: {
        "A": {"threshold": a, "argmax": a},
        "B": {S.S_OP: {"recall": rec, "precision": prec}, "P_argmax": {"recall": 0.1, "precision": 1.0}},
    }}}  # fmt: skip


def _runs(db_rec: list[float], db_prec: list[float], db_macro: float = 0.0) -> list[dict]:
    out = []
    for i, (r, p) in enumerate(zip(db_rec, db_prec, strict=True)):
        out += [_run("A", 42 + i, 0.60, 0.70, 0.90), _run("B", 42 + i, 0.60 + r, 0.70 + p, 0.90 + db_macro)]
    return out


def test_pairs_by_seed_and_drops_an_unpaired_seed_from_both_arms() -> None:
    runs = _runs([0.1] * 5, [0.0] * 5) + [_run("A", 99, 0.5, 0.5, 0.9)]  # seed 99 only in arm A
    agg = S.aggregate(runs)
    assert agg["seeds"] == [42, 43, 44, 45, 46] and agg["dropped_unpaired"] == [99]
    m = agg["variants"][S.HEADLINE][f"B/{S.S_OP}/recall"]
    assert m["B_minus_A"]["per_seed"] == [0.1] * 5


def test_decision_rule_promoted_not_promoted_inconclusive() -> None:
    good = S.aggregate(_runs([0.10, 0.12, 0.09, 0.11, 0.10], [0.01, -0.01, 0.0, 0.01, 0.0]))["decision"]
    assert good["outcome"] == "PROMOTED"
    # a noisy recall gain whose interval includes 0 is INCONCLUSIVE, not promoted and not refuted
    noisy = S.aggregate(_runs([0.30, -0.20, 0.25, -0.10, 0.20], [0.0] * 5))["decision"]
    assert noisy["outcome"] == "INCONCLUSIVE" and not noisy["c1_recall_interval_lower_bound_above_0"]["pass"]
    # base-set degradation beyond 0.02 refutes regardless of the safety gain
    assert S.aggregate(_runs([0.1] * 5, [0.0] * 5, db_macro=-0.03))["decision"]["outcome"] == "NOT PROMOTED"
    # a precision drop that is established (interval entirely below -0.05) refutes
    drop = S.aggregate(_runs([0.1, 0.1, 0.11, 0.1, 0.09], [-0.2, -0.21, -0.19, -0.2, -0.2]))["decision"]
    assert drop["outcome"] == "NOT PROMOTED"
    # recall interval wholly at or below zero refutes
    assert S.aggregate(_runs([-0.1, -0.11, -0.09, -0.1, -0.1], [0.0] * 5))["decision"]["outcome"] == "NOT PROMOTED"


def test_render_wraps_every_number_in_a_metrics_block_and_states_the_outcome(tmp_path) -> None:  # noqa: ANN001
    import json

    runs = _runs([0.1, 0.12, 0.09, 0.11, 0.1], [0.0, 0.01, -0.01, 0.0, 0.0])
    p = tmp_path / "agg.json"
    p.write_text(json.dumps(S.aggregate(runs)), encoding="utf-8")
    md = S.render_seeds(str(p))
    assert md.count("<!-- METRICS:START:") == 4 == md.count("<!-- METRICS:END -->")
    assert "Seed-level outcome (Amendment 8): PROMOTED" in md and "silver (panel) labels" in md
    assert "ABLATION" in md
