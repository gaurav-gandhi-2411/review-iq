from __future__ import annotations

import math

from eval.experiments import noise_floor_baseline as nf


def _row(i: str, **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": i,
        "gold": None,
        "baseline_pred": None,
        "variant_pred": None,
        "baseline_sentiment": "positive",
        "variant_sentiment": "positive",
    }
    base.update(kw)
    return base


def test_select_ids_is_deterministic_and_sorted() -> None:
    rows = {f"h{i:02d}": _row(f"h{i:02d}", gold=True if i % 2 else None) for i in range(40)}
    rows["h00"] = _row("h00", variant_pred=True)  # tier 1
    rows["h02"] = _row("h02", baseline_sentiment="mixed")  # tier 2
    a, b = nf.select_ids(rows, 10), nf.select_ids(rows, 10)
    assert a == b == sorted(a)
    assert len(a) == 10 and "h00" in a and "h02" in a


def test_select_ids_refuses_to_truncate_tiers() -> None:
    rows = {f"h{i:02d}": _row(f"h{i:02d}", variant_pred=True) for i in range(5)}
    try:
        nf.select_ids(rows, 3)
    except ValueError as e:
        assert "N_TARGET" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_fisher_exact_known_values() -> None:
    assert math.isclose(nf.fisher_exact_two_sided(1, 1, 1, 1), 1.0)
    # classic tea-tasting table [[3,1],[1,3]] two-sided p = 0.4857...
    assert math.isclose(nf.fisher_exact_two_sided(3, 1, 1, 3), 34 / 70, rel_tol=1e-9)
    assert nf.fisher_exact_two_sided(8, 0, 0, 8) < 0.001


def test_summarise_counts() -> None:
    rows = [
        {
            "id": "a",
            "gold": True,
            "baseline_pred": None,
            "repeat_pred": True,
            "variant_pred": True,
            "baseline_score": 0.0,
            "repeat_score": 1.0,
            "variant_score": 1.0,
            "baseline_sentiment": "mixed",
            "repeat_sentiment": "mixed",
            "variant_sentiment": "positive",
        },
        {
            "id": "b",
            "gold": None,
            "baseline_pred": True,
            "repeat_pred": True,
            "variant_pred": None,
            "baseline_score": 0.0,
            "repeat_score": 0.0,
            "variant_score": 1.0,
            "baseline_sentiment": "positive",
            "repeat_sentiment": "positive",
            "variant_sentiment": "positive",
        },
    ]
    s = nf.summarise(rows)
    assert s["coverage"] == {"baseline": 1, "repeat": 2, "variant": 1}
    assert s["wrong_committed"] == {"baseline": 1, "repeat": 1, "variant": 0}
    assert s["commit_changed_vs_baseline"] == {"repeat": 1, "variant": 2}
    assert s["sentiment_changed_vs_baseline"] == {"repeat": 0, "variant": 1}
    assert s["baseline_mixed"] == {"n": 1, "left_mixed_repeat": 0, "left_mixed_variant": 1}
