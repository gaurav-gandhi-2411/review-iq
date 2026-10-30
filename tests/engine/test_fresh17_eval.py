"""Amendment 17 criteria F1-F4 on synthetic logits: the floor tau adds routes, the sets, and each verdict threshold."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip(
    "onnxruntime"
)  # the module imports the serving runtime; the CI venv skips the serving extras together
from engine.intent import fresh17_eval as E  # noqa: E402
from tests.engine.test_fresh16_eval import CLASSES, labels_for, thresholds  # noqa: E402


def items() -> list[dict]:
    out = []
    for b, n in (("A", 60), ("B", 60)):
        out += [{"id": f"fresh17-{b}-food-{k:04d}", "block": b, "category": "food", "stars": 5.0 if (b == "B" or k % 2 == 0) else 2.0}
                for k in range(n)]  # fmt: skip
    return out


def logits(p_yes: list[float]) -> np.ndarray:
    """needs_action probability set exactly by logit difference; the other heads say a / positive / low (never route)."""
    lg = np.zeros((len(p_yes), 8))
    for i, p in enumerate(p_yes):
        lg[i, 0:2] = [np.log(p), np.log(1 - p)]
        lg[i, 2:4] = [3, 0]
        lg[i, 4:6] = [3, 0]
        lg[i, 6:8] = [3, 0]
    return lg


def test_the_floor_routes_low_probability_items_that_r1_misses(tmp_path: Path) -> None:
    th = thresholds(tmp_path)
    r1, rp = E.routes(th, logits([0.9, 0.08, 0.06, 0.01]), tau=0.07)
    assert r1.tolist() == [True, False, False, False]
    assert rp.tolist() == [True, True, False, False]


def test_verdicts_follow_the_registered_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    th = thresholds(tmp_path)
    its = items()
    ids = [r["id"] for r in its]
    yes = {i for i in ids if i.endswith(("0", "5"))}  # 12 per block are gold-yes
    p = [
        0.08 if i in yes else 0.01 for i in ids
    ]  # every positive caught only by the floor, nothing else routed
    llm = {i: {"ans": {"needs_action": {"label": "yes"}}} for i in yes}
    monkeypatch.setattr(E, "MIN_POS", {"A": 10, "S4": 10})
    res = E.evaluate(its, labels_for(its, yes), th, logits(p), 0.07, llm)
    assert (
        res["F1_recall_block_A"]["recall_r1_plus"]["k"] == 12
        and res["F1_recall_block_A"]["recall_r1"]["k"] == 0
    )
    assert res["F3_routed_share_block_A"]["met"] is True  # 12/60 routed
    assert res["F4_non_inferior_to_llm_block_A"]["llm_missing_rows"] == 0
    # n = 12: perfect recall has a Wilson lower bound below 0.90, so F1 fails on sample size alone (honest small-n behaviour)
    assert res["F1_recall_block_A"]["met"] is False and "F1" in res["verdict"]["failing"]
    assert res["verdict"]["criteria_met"] is False


def test_routed_share_above_the_cap_fails_f3(tmp_path: Path) -> None:
    th = thresholds(tmp_path)
    its = items()
    res = E.evaluate(its, labels_for(its, set()), th, logits([0.5] * len(its)), 0.07, {})
    assert (
        res["F3_routed_share_block_A"]["r1_plus"]["rate"] == 1.0
        and res["F3_routed_share_block_A"]["met"] is False
    )


def test_classes_match_the_shared_fixture() -> None:
    assert list(CLASSES) == ["needs_action", "broad_intent", "sentiment", "urgency"]
