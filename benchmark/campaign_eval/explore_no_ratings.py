"""EXPLORATORY (not pre-registered), VALIDATION products only: the frozen detector row with the
rating-based signals masked, which is what production runs today (extraction rows carry no
rating). The sealed set is not touched. Writes reports/campaign_eval/exploratory_no_ratings_validation.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from benchmark.campaign_eval import analysis as an
from benchmark.campaign_eval.evaluate import Selection, evaluate_stream, load_pickle
from benchmark.campaign_eval.generator import split_configs
from benchmark.campaign_eval.grid import grid_from_params

OUT = Path("reports/campaign_eval")


def main() -> None:
    frozen = json.loads((OUT / "frozen_params_grid_v2.json").read_text())["budgets"]["1.0"]
    keys = json.loads((OUT / "split.json").read_text())["products"]["validation"]
    streams = {}
    for corpus in ("amazon", "sephora"):
        streams.update(load_pickle(f"streams_{corpus}.pkl"))
    cfgs = split_configs()["validation"]
    grid = grid_from_params([frozen["detector"]["params"]])
    b1 = np.array([frozen["b1"]["params"]["theta"]])
    out: dict = {"label": "EXPLORATORY, validation split, SYNTHETIC injections", "variants": {}}
    for name, no_ratings in (("with_ratings", False), ("without_ratings", True)):
        sel = Selection(grid, b1, np.array([]), no_ratings=no_ratings)
        results = [evaluate_stream(streams[k], cfgs, sel) for k in keys]
        out["variants"][name] = {m: an.summarize(an.Frame(results, m, 0)) for m in ("det", "b1")}
    slim = {
        v: {
            m: {
                "false_alerts_per_product_month": s["false_alerts_per_product_month"]["value"],
                "recall": s["recall"]["value"],
                "recall_ci95": s["recall"]["ci95"],
            }
            for m, s in d.items()
        }
        for v, d in out["variants"].items()
    }
    (OUT / "exploratory_no_ratings_validation.json").write_text(
        json.dumps(slim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(slim, indent=2))


if __name__ == "__main__":
    main()
