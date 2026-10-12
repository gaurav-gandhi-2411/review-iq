"""Re-pilot 2 numbers (spec Amendment 4): agreement per task on the RANDOM 300 and the MINED 60, consensus class
distribution with Wilson intervals, the rare-class sample-size rule, parse health, and measured cost per item.

    python -m engine.labelling.repilot2_report JUDGES_DIR STATUS_JSON OUT.json

The output carries no review text.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling import prompts_v3 as P3
from engine.labelling.repilot_report import consensus_label

Z = 1.96
TEST_SHARE = 0.15  # sealed test share, Amendment 2
POSITIVES_FOR_PRECISION = 138  # half-width 0.05 at precision 0.90: 1.96**2 * 0.09 / 0.05**2


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return (round(c - h, 4), round(c + h, 4))


def main() -> None:
    judges, items = A.load_judges(Path(sys.argv[1]))
    status = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    mined = {
        i for i, recs in items.items() if next(iter(recs.values()))["stratum"].startswith("mined_")
    }
    random_ids = set(items) - mined
    out: dict = {"judges": judges, "n_random": len(random_ids), "n_mined": len(mined)}

    for name, ids in (("random", random_ids), ("mined", mined), ("all", set(items))):
        tasks = A.build_tasks(judges, {i: items[i] for i in ids}, "v3")
        for v in tasks.values():
            if isinstance(v, dict) and "alpha" in v:
                v["gate"] = A.verdict(v)
        out[f"tasks_{name}"] = tasks

    def consensus(key: str, ids: set[str]) -> dict[str | None, int]:
        labs = []
        for i in sorted(ids):
            vals = [
                items[i][j]["text"][key] if items[i].get(j) and items[i][j].get("text") else None
                for j in judges
            ]
            labs.append(consensus_label(vals))
        return Counter(labs)

    dist: dict = {}
    for key in ("needs_action", "broad_intent", "sentiment", "urgency"):
        for name, ids in (("random", random_ids), ("mined", mined)):
            c = consensus(key, ids)
            n_with = sum(v for k, v in c.items() if k is not None)
            dist[f"{key}_{name}"] = {
                "n": len(ids),
                "no_consensus": c.get(None, 0),
                "classes": {
                    str(k): {"n": v, "share_of_consensus": round(v / n_with, 4) if n_with else None,
                             "wilson95": wilson(v, n_with)}
                    for k, v in sorted(c.items(), key=lambda kv: str(kv[0])) if k is not None
                },
            }  # fmt: skip
    out["consensus_distribution"] = dist

    # M2d: rare-class sample-size rule (Amendment 4)
    c_all_r = consensus("broad_intent", random_ids)
    c_all_m = consensus("broad_intent", mined)
    n_r = sum(v for k, v in c_all_r.items() if k is not None)
    support = {}
    for cls in P3.BROAD_INTENTS:
        kr, km = c_all_r.get(cls, 0), c_all_m.get(cls, 0)
        lo, hi = wilson(kr, n_r)
        need = {
            tag: (math.ceil(POSITIVES_FOR_PRECISION / (p * TEST_SHARE)) if p > 0 else None)
            for tag, p in (("point", kr / n_r if n_r else 0), ("at_wilson_upper", hi),
                           ("at_wilson_lower", lo))
        }  # fmt: skip
        support[cls] = {
            "random": kr, "mined": km, "total": kr + km, "measurable_30plus": kr + km >= 30,
            "prevalence_random": round(kr / n_r, 4) if n_r else None,
            "prevalence_wilson95": (lo, hi),
            "labelled_corpus_needed_for_138_test_positives": need,
        }  # fmt: skip
    out["broad_intent_support"] = support

    out["parse"] = {
        j: {
            "failed": sum(1 for recs in items.values() if recs.get(j) and recs[j]["text"] is None),
            "retried": sum(1 for recs in items.values() if recs.get(j) and recs[j].get("retried")),
            "aspects_dropped_unverifiable": sum(
                recs[j].get("aspects_dropped_unverifiable", 0) for recs in items.values() if recs.get(j)
            ),
        }
        for j in judges
    }  # fmt: skip

    n_items = len(items)
    cost = {}
    for name, m in status["models"].items():
        tail = json.loads(m["tail"].strip().splitlines()[-1]) if m.get("tail") else {}
        sec = tail.get("seconds") or m.get("seconds")
        cost[name] = {"model": m.get("model"), "seconds_total": sec,
                      "seconds_per_item": round(sec / n_items, 3) if sec else None}  # fmt: skip
    out["measured_cost"] = cost
    Path(sys.argv[3]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {sys.argv[3]}")


if __name__ == "__main__":
    main()
