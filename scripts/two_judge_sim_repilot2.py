from __future__ import annotations

import itertools
import json
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling.repilot_report import consensus_label

root = Path(r"C:\Users\gaura\ml-projects\review-iq-wt-eng")
judges, items = A.load_judges(Path(r"D:\ml-cache\kaggle-out\judges3\out\judges"))
status = json.loads(
    Path(r"D:\ml-cache\kaggle-out\judges3\out\status.json").read_text(encoding="utf-8")
)
sec = {
    n: json.loads(m["tail"].strip().splitlines()[-1])["seconds"] / 360
    for n, m in status["models"].items()
}
rand = [i for i, r in items.items() if not next(iter(r.values()))["stratum"].startswith("mined_")]
KEYS = ("needs_action", "broad_intent", "sentiment", "urgency")


def lab(i: str, j: str, k: str):
    r = items[i].get(j)
    return r["text"][k] if r and r.get("text") else None


truth = {k: {i: consensus_label([lab(i, j, k) for j in judges]) for i in rand} for k in KEYS}
rows = []
for a, b in itertools.combinations(judges, 2):
    for c in judges:
        if c in (a, b):
            continue
        res = {}
        third_calls = 0
        per_task = {k: {"labelled": 0, "agree": 0, "den": 0} for k in KEYS}
        for i in rand:
            disagree = any(lab(i, a, k) != lab(i, b, k) for k in KEYS)
            third_calls += disagree
            for k in KEYS:
                va, vb, vc = lab(i, a, k), lab(i, b, k), lab(i, c, k)
                if va is not None and va == vb:
                    out = va
                elif disagree and vc is not None and vc in (va, vb):
                    out = vc
                else:
                    out = None
                t = truth[k][i]
                per_task[k]["labelled"] += out is not None
                if t is not None:
                    per_task[k]["den"] += 1
                    per_task[k]["agree"] += out == t
        n = len(rand)
        cost = sec[a] + sec[b] + sec[c] * third_calls / n
        rows.append({
            "pair": [a, b], "tiebreak": c, "third_call_rate": round(third_calls / n, 3),
            "sec_per_item": round(cost, 3),
            "tasks": {k: {"coverage": round(v["labelled"] / n, 3),
                          "agree_with_4panel_consensus": round(v["agree"] / v["den"], 3)}
                      for k, v in per_task.items()},
        })  # fmt: skip
rows.sort(key=lambda r: r["sec_per_item"])
four = sum(sec.values())
out = {"n_random": len(rand), "four_judge_sec_per_item": round(four, 3), "schemes": rows}
Path(root / "reports/labelling/pilot3/two_judge_sim.json").write_text(
    json.dumps(out, indent=1) + "\n", encoding="utf-8"
)
print("4-judge", round(four, 2), "s/item")
for r in rows[:6]:
    print(
        r["pair"],
        r["tiebreak"],
        r["third_call_rate"],
        r["sec_per_item"],
        {k: (v["coverage"], v["agree_with_4panel_consensus"]) for k, v in r["tasks"].items()},
    )
