"""Amendment 16: choose the input-length arm on VALIDATION ONLY, by the rule fixed before any arm was trained.

    PYTHONPATH=. python -m engine.intent.run4_select --arms H128=<dir>:128:head,H256=<dir>:256:head,... \
        --labels reports/intent/labels/labels.json --labels16 <train16 labels json> --out selection.json

Per arm (fp32 weights, production numerics are irrelevant to the choice: it is made on the fp32 models, quantisation comes after, for the chosen arm only):
  score  = R1 routing recall over the gold needs_action=yes items of the ORIGINAL validation split plus T-val (the targeted validation items), with the
           temperature and thresholds fitted on the original validation split only (`scripts/build_classify_thresholds.build`, as for every served model);
  f1     = mean over the four served tasks of macro-F1 on the original validation split;
  routed = R1 routed share on the original validation split.
Eligible: f1 within 0.01 of the best arm's AND routed share <= 0.30 (routing everything must not win).
Chosen: the eligible arm with the highest score; arms within 0.01 of that score prefer the shorter length, then head over head_tail. `ranking` applies the
same rule repeatedly (the fallback order if the chosen arm's quantisation fails its 0.90 agreement criterion). No sealed or fresh item is read.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
from app.core.classify import encoder_first as EF
from app.core.classify import runtime

SERVED = ("needs_action", "broad_intent", "sentiment", "urgency")
F1_TOLERANCE = 0.01
SCORE_TIE = 0.01
MAX_ROUTED_SHARE = 0.30


def _routed(th: runtime.Thresholds, logits: dict[str, np.ndarray]) -> list[bool]:
    return [
        bool(EF.route_r1(d).route) for d in runtime.decisions(th, {t: logits[t] for t in SERVED})
    ]


def arm_scores(arm_dir: Path, labels: dict, labels16: dict) -> dict:
    from scripts.build_classify_thresholds import build

    from engine import metrics as M

    labels_path = arm_dir / "labels_for_thresholds.json"
    labels_path.write_text(
        json.dumps(labels), encoding="utf-8"
    )  # build() reads a file; same document
    try:
        doc = build(arm_dir, labels_path, None, "run4_arm")
    finally:
        labels_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "th.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        th = runtime.load_thresholds(p)
    val_ids = json.loads((arm_dir / "pred_needs_action.json").read_text(encoding="utf-8"))[
        "splits"
    ]["val"]["ids"]
    v = np.load(arm_dir / "logits_val.npz")
    tv = np.load(arm_dir / "extra_val_targeted_logits.npz")
    tv_ids = json.loads((arm_dir / "extra_val_targeted_ids.json").read_text(encoding="utf-8"))
    r_val, r_tv = _routed(th, v), _routed(th, tv)
    gold_v = [labels["labels"]["val"]["needs_action"].get(i) == "yes" for i in val_ids]
    gold_t = [labels16["labels"]["needs_action"].get(i) == "yes" for i in tv_ids]
    hit_v = sum(r for r, g in zip(r_val, gold_v, strict=True) if g)
    hit_t = sum(r for r, g in zip(r_tv, gold_t, strict=True) if g)
    n_pos = sum(gold_v) + sum(gold_t)
    f1s = {}
    for t in SERVED:
        lab = labels["labels"]["val"][t]
        cl = list(doc["tasks"][t]["classes"])
        idx = [k for k, i in enumerate(val_ids) if i in lab]
        y = np.array([cl.index(lab[val_ids[k]]) for k in idx])
        f1s[t] = float(M.macro_f1(y, v[t][idx].argmax(1)))
    return {
        "score": (hit_v + hit_t) / max(1, n_pos),
        "n_gold_yes": n_pos, "n_gold_yes_val": sum(gold_v), "n_gold_yes_targeted": sum(gold_t),
        "recall_val": hit_v / max(1, sum(gold_v)), "recall_targeted": hit_t / max(1, sum(gold_t)),
        "routed_share_val": sum(r_val) / len(r_val), "f1_by_task": f1s, "f1": float(np.mean(list(f1s.values()))),
    }  # fmt: skip


def _prefer(near: list[str], arms: dict[str, dict]) -> str:
    return min(
        near, key=lambda a: (arms[a]["max_len"], 0 if arms[a]["truncation"] == "head" else 1, a)
    )


def select(arms: dict[str, dict]) -> dict:
    """arms[name] = {"max_len", "truncation", "score", "f1", "routed_share_val", ...} -> selection document."""
    if not arms:
        return {"chosen": None, "ranking": [], "eligible": [], "reason": "no arms"}
    best_f1 = max(a["f1"] for a in arms.values())
    eligible = [
        n
        for n, a in arms.items()
        if a["f1"] >= best_f1 - F1_TOLERANCE and a["routed_share_val"] <= MAX_ROUTED_SHARE
    ]
    ranking: list[str] = []
    rest = list(eligible)
    while rest:
        top = max(arms[n]["score"] for n in rest)
        pick = _prefer([n for n in rest if arms[n]["score"] >= top - SCORE_TIE], arms)
        ranking.append(pick)
        rest.remove(pick)
    return {
        "rule": "eligible: f1 within 0.01 of best and routed share <= 0.30; highest score, ties within 0.01 prefer shorter then head",
        "best_f1": best_f1, "eligible": eligible, "ranking": ranking,
        "chosen": ranking[0] if ranking else None,
        "reason": None if ranking else "no eligible arm (stop and report)",
        "arms": arms,
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True, help="NAME=dir:max_len:truncation,...")
    ap.add_argument("--labels", type=Path, default=Path("reports/intent/labels/labels.json"))
    ap.add_argument("--labels16", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    labels = json.loads(a.labels.read_text(encoding="utf-8"))
    labels16 = json.loads(a.labels16.read_text(encoding="utf-8"))
    arms: dict[str, dict] = {}
    for spec in a.arms.split(","):
        name, rest = spec.split("=", 1)
        d, max_len, trunc = rest.rsplit(":", 2)
        arms[name] = {
            "max_len": int(max_len),
            "truncation": trunc,
            **arm_scores(Path(d), labels, labels16),
        }
    res = select(arms)
    a.out.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("eligible", "ranking", "chosen")}))


if __name__ == "__main__":
    main()
