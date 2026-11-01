from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")  # absent from the CI venv: skip, as test_engine_core does

from engine.intent import baselines as B  # noqa: E402
from engine.intent import evaluate as E  # noqa: E402
from engine.intent import report as R  # noqa: E402
from scripts.check_no_hardcoded_metrics import find_violations  # noqa: E402

CL = ["yes", "no"]


def synthetic_report() -> dict:
    rng = np.random.default_rng(7)
    lab = {
        s: {"needs_action": {}, "urgency": {}, "sentiment": {}} for s in ("train", "val", "test")
    }
    sp = {}
    for s, n in (("train", 80), ("val", 200), ("test", 200)):
        ids, probs = [], []
        for k in range(n):
            i = f"{s}{k}"
            g = int(rng.random() < 0.5)
            lab[s]["needs_action"][i] = CL[g]
            if s != "test" or k < 10:  # urgency: only 10 sealed items -> insufficient data
                lab[s]["urgency"][i] = "low"
            ok = rng.random() < 0.9
            c = rng.uniform(0.6, 0.99) if ok else rng.uniform(0.5, 0.6)
            p = [0.0, 0.0]
            p[g if ok else 1 - g] = c
            p[1 - g if ok else g] = 1 - c
            ids.append(i)
            probs.append(p)
        sp[s] = {"ids": ids, "probs": probs}
    classes = {
        "needs_action": CL,
        "urgency": ["low", "high"],
        "sentiment": ["positive", "negative"],
    }
    preds = {t: {"task": t, "classes": c, "splits": sp} for t, c in list(classes.items())[:2]}
    labels = {"classes": classes, "labels": lab}
    strata = {i: "food" for s in sp.values() for i in s["ids"]}
    tasks = E.evaluate_all(labels, preds, strata, 100)
    B.attach_baselines(tasks, labels, preds, None, None, strata, 100)
    prov = {"evaluation_commit": "abc1234", "training_metadata": {"commit": "def5678"},
            "artifacts_sha256_16": {"labels.json": "0123456789abcdef"}, "label_basis": "silver"}  # fmt: skip
    return {
        "provenance": prov,
        "n_boot": 100,
        "seed": 42,
        "bootstrap_unit": "items",
        "tasks": tasks,
    }


def test_report_renders_every_task_and_passes_the_hardcoded_metrics_check(tmp_path: Path) -> None:
    rep = synthetic_report()
    md = R.render(json.loads(json.dumps(rep)), "reports/intent/eval_x.json")
    assert "| needs_action |" in md and "| urgency | 10 |" in md and "| sentiment |" in md
    assert "insufficient data" in md  # the 10-item task is never given a metric
    assert "majority" in md and "ORACLE" in md and "abc1234" in md
    path = tmp_path / "review-intent-model.md"
    path.write_text(md, encoding="utf-8")
    assert find_violations(path) == []  # every figure sits inside a METRICS block
    # the check is live: with the markers removed the same text is flagged, so the pass above means something
    stripped = tmp_path / "stripped.md"
    stripped.write_text(
        re.sub(r"<!-- METRICS:[^>]*-->", "", md.replace("0.", "50.0%  accuracy 0.")),
        encoding="utf-8",
    )
    assert find_violations(stripped)
