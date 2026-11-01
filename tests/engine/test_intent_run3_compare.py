from __future__ import annotations

import pytest

pytest.importorskip("sklearn")  # absent from the CI venv: skip, as test_intent_evaluate does


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
