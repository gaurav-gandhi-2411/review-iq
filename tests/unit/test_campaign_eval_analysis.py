"""Selection and bootstrap mechanics of the campaign evaluation (benchmark/campaign_eval/analysis.py)."""

from __future__ import annotations

import numpy as np
from benchmark.campaign_eval import analysis as an


def test_select_column_respects_both_budgets_and_ties() -> None:
    tune = {"far": np.array([0.5, 0.9, 2.0, 0.4]), "rec": np.array([0.3, 0.6, 0.9, 0.6])}
    val = {"far": np.array([0.5, 1.5, 0.5, 0.4])}
    assert an.select_column(tune, val, 1.0) == 3  # col1 fails validation, col2 fails tuning
    assert an.select_column(tune, val, 0.1) is None
    assert an.select_column(tune, val, 1.0, allowed=np.array([True, True, True, False])) == 0


def test_bootstrap_resamples_products_and_is_deterministic() -> None:
    results = []
    for s in range(30):
        results.append(
            {
                "clean": {"months": 10.0, "fa": {"det": np.array([s % 3])}},
                "inj": [
                    {"size": 5, "type": "a/b", "config_id": f"c{j}", "det": {
                        "det": np.array([(s * 3 + j) % 5 == 0]), "ttd_n": np.array([3]),
                        "ttd_h": np.array([2.0])}}
                    for j in range(4)
                ],
            }
        )  # fmt: skip
    f = an.Frame(results, "det", 0)
    out1, out2 = an.summarize(f), an.summarize(f)
    assert out1 == out2
    lo, hi = out1["recall"]["ci95"]
    assert lo <= out1["recall"]["value"] <= hi and 0 <= lo < hi <= 1
    assert out1["false_alerts_per_product_month"]["alert_episodes"] == sum(s % 3 for s in range(30))


def test_production_defaults_equal_the_frozen_tuned_parameters() -> None:
    """The shipped CampaignParams defaults must be the row frozen BEFORE the sealed run."""
    import json
    from pathlib import Path

    from app.core.detectors.campaign_signals import DEFAULT_PARAMS

    frozen = json.loads(Path("reports/campaign_eval/frozen_params_grid_v2.json").read_text())
    pick = frozen["budgets"]["1.0"]["detector"]["params"]
    assert {
        "similarity": DEFAULT_PARAMS.similarity,
        "k_min": DEFAULT_PARAMS.k_min,
        "theta_burst": DEFAULT_PARAMS.theta_burst,
        "theta_rating": DEFAULT_PARAMS.theta_rating,
        "strong_multiplier": DEFAULT_PARAMS.strong_multiplier,
        "use_mismatch": DEFAULT_PARAMS.use_mismatch,
    } == pick
