"""Amendment 18: the one registered evaluation of R1+ against the all-LLM path on fresh18 (S4 only). Written before any fresh18 label exists.

    PYTHONPATH=. python -m engine.intent.fresh18_eval subsample --labels reports/intent/labels_fresh18/fresh18_labels.json \
        --out reports/intent/fresh18/subsample_120b.json
    PYTHONPATH=. python -m engine.intent.fresh18_eval score --manifest reports/labelling/fresh18/manifest.json \
        --labels reports/intent/labels_fresh18/fresh18_labels.json --run2-logits <fresh18 run-2 logits npz (logits, ids)> \
        --tau-file reports/intent/a17/tau.json --llm20 <20b shards dir> [--llm120 <120b rows jsonl>] --out reports/intent/fresh18/result.json

P1 (docs/specs/review-intent.md Amendment 18): the paired difference recall(R1+) minus recall(gpt-oss-20b) on fresh18 S4 gold-yes, percentile
95 percent CI from a paired bootstrap (10,000 resamples, seed 42, items resampled); pass iff the lower bound > -0.05. Everything else is
reported, not gating. An empty 20b row counts as "no" in P1 and is excluded in a sensitivity analysis. Ids and numbers only.
"""

from __future__ import annotations

import argparse
import json
import random
from math import comb
from pathlib import Path

import numpy as np
from app.core.classify import runtime

from engine.intent.fresh15_eval import load_llm, paired_delta, rate
from engine.intent.fresh17_eval import THRESHOLDS, routes

MARGIN = 0.05
N_BOOT = 10_000
SUBSAMPLE_120B = 200
A17_S4_LOWER = 0.85  # reported for continuity with A17's F2, not a criterion here


def mcnemar_exact(router: np.ndarray, llm: np.ndarray) -> dict:
    """Exact two-sided McNemar: binomial test on the discordant pairs."""
    b, c = int((router & ~llm).sum()), int((~router & llm).sum())
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2**n)
    return {"router_only": b, "llm_only": c, "p": round(p, 6)}


def says_yes(row: dict | None) -> bool:
    ans = (row or {}).get("ans")
    return bool(ans and ans.get("needs_action", {}).get("label") == "yes")


def is_empty(row: dict | None) -> bool:
    return not (row or {}).get("ans")


def gold_yes_ids(labels: dict) -> list[str]:
    return sorted(i for i, v in labels["labels"]["needs_action"].items() if v == "yes")


def subsample_120b(labels: dict) -> list[str]:
    """The registered 120b subsample: the first min(200, n) ids of a Random(42) shuffle of the sorted gold-yes ids."""
    ids = gold_yes_ids(labels)
    random.Random(42).shuffle(ids)
    return ids[:SUBSAMPLE_120B]


def compare(router: np.ndarray, llm: np.ndarray) -> dict:
    return {"router_recall": rate(int(router.sum()), len(router)), "llm_recall": rate(int(llm.sum()), len(llm)),
            "paired": paired_delta(router.astype(float), llm.astype(float), n_boot=N_BOOT), "mcnemar": mcnemar_exact(router, llm),
            "missed_by_both": int((~router & ~llm).sum())}  # fmt: skip


def evaluate(
    items: list[dict],
    labels: dict,
    logits: np.ndarray,
    th: runtime.Thresholds,
    tau: float,
    llm20: dict,
    llm120: dict,
) -> dict:
    ids = [r["id"] for r in items]
    r1, rp = routes(th, logits, tau)
    na = labels["labels"]["needs_action"]
    pos = [k for k, i in enumerate(ids) if na.get(i) == "yes"]
    neg = [k for k, i in enumerate(ids) if na.get(i) == "no"]
    y20 = np.array([says_yes(llm20.get(ids[k])) for k in pos])
    p1 = compare(rp[pos], y20)
    p1["met"] = bool(p1["paired"]["boot95"][0] > -MARGIN)
    res: dict = {
        "n_items": len(ids), "n_gold_yes": len(pos), "n_gold_no": len(neg), "no_consensus": len(ids) - len(pos) - len(neg),
        "size_rule_met": len(pos) >= 300,
        "llm20": {"missing": sum(ids[k] not in llm20 for k in pos + neg), "empty": sum(is_empty(llm20.get(ids[k])) for k in pos + neg)},
        "P1_r1_plus_vs_20b": {**p1, "margin": MARGIN},
        "verdict": {"P1": p1["met"]},
    }  # fmt: skip
    ne = [k for k in pos if not is_empty(llm20.get(ids[k]))]
    if len(ne) != len(pos):
        res["sensitivity_empty_20b_excluded"] = compare(
            rp[ne], np.array([says_yes(llm20[ids[k]]) for k in ne])
        )
    rp_rec = rate(int(rp[pos].sum()), len(pos))
    res["report_only"] = {
        "r1_plus_recall": {**rp_rec, "lower_vs_a17_0_85": bool(rp_rec["wilson95"] and rp_rec["wilson95"][0] >= A17_S4_LOWER)},
        "r1_recall": rate(int(r1[pos].sum()), len(pos)),
        "r1_plus_routed_share_all": rate(int(rp.sum()), len(ids)),
        "r1_plus_routed_share_gold_no": rate(int(rp[neg].sum()), len(neg)),
        "llm20_yes_rate_gold_no": rate(sum(says_yes(llm20.get(ids[k])) for k in neg), len(neg)),
    }  # fmt: skip
    sub = set(subsample_120b(labels))
    got = [k for k in pos if ids[k] in sub and ids[k] in llm120]
    if got:
        cmp120 = compare(rp[got], np.array([says_yes(llm120[ids[k]]) for k in got]))
        res["report_only"]["r1_plus_vs_120b_subsample"] = {
            **cmp120,
            "n_registered": len(sub),
            "complete": len(got) == len(sub),
        }
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("subsample")
    s.add_argument("--labels", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("score")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--run2-logits", type=Path, required=True)
    p.add_argument("--tau-file", type=Path, required=True)
    p.add_argument("--llm20", type=Path, required=True)
    p.add_argument("--llm120", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    labels = json.loads(a.labels.read_text(encoding="utf-8"))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.cmd == "subsample":
        a.out.write_text(json.dumps({"rule": "first min(200, n) of Random(42) shuffle of sorted gold-yes ids", "ids": subsample_120b(labels)},
                                    indent=1) + "\n", encoding="utf-8")  # fmt: skip
        return
    items = json.loads(a.manifest.read_text(encoding="utf-8"))["items"]
    z = np.load(a.run2_logits)
    if [str(x) for x in z["ids"]] != [r["id"] for r in items]:
        raise SystemExit("logits are for different items than the manifest")
    th = runtime.load_thresholds(THRESHOLDS)
    llm120 = {}
    if a.llm120 and a.llm120.exists():
        for ln in a.llm120.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                llm120[r["id"]] = r
    tau = float(json.loads(a.tau_file.read_text(encoding="utf-8"))["tau"])
    res = evaluate(items, labels, z["logits"], th, tau, load_llm(a.llm20), llm120)
    res = {
        "spec": "docs/specs/review-intent.md Amendment 18",
        "thresholds_sha256": th.sha256,
        "tau": tau,
        **res,
    }
    a.out.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["verdict"]))


if __name__ == "__main__":
    main()
