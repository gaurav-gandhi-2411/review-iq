from __future__ import annotations

from engine.intent import llm_moat as M
from engine.intent import llm_moat_paired as P


def _row(i: str, label: str, conf: int) -> dict:
    return {"id": i, "ans": {"sentiment": {"label": label, "confidence": conf}}, "tokens": 1}


GOLD = {f"i{k:02d}": ("positive" if k % 2 else "negative") for k in range(40)}


def _arm(wrong: set[str], conf: int, order: list[str]) -> list[dict]:
    return [_row(i, "neutral" if i in wrong else GOLD[i], conf) for i in order]


def test_identical_arms_have_zero_difference_and_row_order_is_irrelevant():
    ids = sorted(GOLD)
    a = _arm({"i03"}, 90, ids)
    b = _arm({"i03"}, 90, ids[::-1])
    r = P.paired_task(a, b, GOLD, "sentiment", n_boot=50)
    assert r["n_matched"] == 40
    assert r["accuracy"]["diff"] == 0.0 and r["accuracy"]["diff_ci95"] == [0.0, 0.0]


def test_only_matched_ids_are_compared_and_sign_is_a_minus_b():
    ids = sorted(GOLD)
    better = _arm(set(), 90, ids)  # A: all correct
    worse = _arm(set(ids[:10]), 90, ids[:30])  # B: 10 of the 30 matched ids wrong, 10 ids missing
    r = P.paired_task(better, worse, GOLD, "sentiment", n_boot=100)
    assert r["n_matched"] == 30
    assert r["accuracy"]["a"] == 1.0 and r["accuracy"]["b"] == round(20 / 30, 4)
    assert r["accuracy"]["diff"] > 0 and r["accuracy"]["diff_ci95"][0] > 0
    assert r["coverage_at_precision_0.95"]["a"] == 1.0


def test_paired_task_is_deterministic():
    ids = sorted(GOLD)
    a, b = _arm({"i01", "i02"}, 80, ids), _arm({"i05"}, 70, ids)
    assert P.paired_task(a, b, GOLD, "sentiment", 50) == P.paired_task(a, b, GOLD, "sentiment", 50)
    assert set(P.paired_task(a, b, GOLD, "sentiment", 5)) >= {"n_matched", "accuracy"} and M.TASKS
