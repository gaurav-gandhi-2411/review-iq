"""S19 (mirrors #265): score.py must compare the EXACT confidence with the reporting bar, not the
3 dp rounded copy, while the serialised record keeps its rounded `confidence` unchanged.

Run locally (CI's unit job runs `tests/` only and never collects benchmark/):
`pytest benchmark/phase2_campaign/test_score_precision.py --no-cov -p no:cacheprovider`.

With the real constants the gate itself cannot hit the boundary: without a residual cluster the
text signal is 0 and confidence is one of {0.09, 0.06, 0.15} (0.15 exactly stays below the bar
both before and after). The gate test therefore perturbs RATING_SIGNAL_WEIGHT to build an exact
0.15001 that rounds to 0.15.
"""

from __future__ import annotations

import json

import pytest
import score
from artifact_filter import ProductArtifactBreakdown
from rating_anomaly import RatingStats


def _stats() -> RatingStats:
    return RatingStats(
        canonical_product="p",
        n_reviews=30,
        std=0.1,
        bimodality_ratio=0.9,
        low_variance=True,
        high_bimodality=True,
    )


def test_gate_uses_exact_confidence_not_rounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(score, "RATING_SIGNAL_WEIGHT", 0.30002)
    # rating_signal 1.0 * no-text discount 0.5 * 0.30002 = 0.15001 exactly above the 0.15 bar
    record = score.score_product("p", ["P"], ProductArtifactBreakdown("p", 30), _stats(), {})

    assert record is not None  # rounded-first comparison (0.15 <= 0.15) dropped it
    assert record["confidence"] == 0.15  # serialised value is still the 3 dp rounding
    assert record["_confidence_exact"] > score.CONFIDENCE_REPORT_THRESHOLD


def test_exactly_at_bar_is_still_dropped() -> None:
    record = score.score_product("p", ["P"], ProductArtifactBreakdown("p", 30), _stats(), {})
    assert record is None  # 0.3 * 0.5 * 1.0 == 0.15 is not > 0.15


def test_count_above_bar_uses_exact_value() -> None:
    flagged = [
        {"confidence": 0.15, "_confidence_exact": 0.15004},
        {"confidence": 0.15, "_confidence_exact": 0.15},
        {"confidence": 0.9, "_confidence_exact": 0.9},
    ]
    assert score.count_above_bar(flagged) == 2


def test_output_record_drops_private_keys_so_jsonl_shape_is_unchanged() -> None:
    record = {"confidence": 0.15, "_confidence_exact": 0.15004, "n_reviews": 30}
    assert json.loads(json.dumps(score.output_record(record))) == {
        "confidence": 0.15,
        "n_reviews": 30,
    }
