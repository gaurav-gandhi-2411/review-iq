"""Scale-labelling report on the SEALED set (spec Amendment 5). No review text in the input or the output.

    python -m engine.labelling.scale_report --manifest reports/labelling/scale/manifest.json \
        --judges reports/labelling/scale/judges_A reports/labelling/scale/judges_S reports/labelling/scale/judges_B \
        --out reports/labelling/scale/sealed_report.json

Per task (C1 needs_action, C2 broad_intent, T2 sentiment, T3 urgency, plus aspect sentiment as in Amendment 4) on the
sealed items, four judges: Krippendorff alpha, Fleiss kappa, pairwise and clear-consensus fractions with the section 5
gate verdict (the headline numbers), then SUPPLEMENTARY Gwet AC1 and raw percent agreement (for skewed binaries; never
used to redefine PASS), Wilson intervals, consensus class counts, and an 'insufficient data' flag for every class with
fewer than 20 sealed positives. Last: the consistency check between the two-judge training rule and the four-judge
consensus on the same sealed items. It is NOT independent evidence (the two-judge labels are built from a subset of
the same votes) and is labelled so.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from engine.labelling import agreement as A
from engine.labelling import two_judge as TJ
from engine.labelling.repilot2_report import wilson
from engine.labelling.repilot_report import consensus_label

PANEL = ("llama31_8b", "qwen3_8b", "gemma2_9b", "mistral_7b")
MIN_POSITIVES = 20  # Amendment 5: fewer sealed positives than this is 'insufficient data'
GATE_KEYS = {
    "needs_action": "C1_needs_action",
    "broad_intent": "C2_broad_intent",
    "sentiment": "T2_sentiment",
    "urgency": "T3_urgency",
}


def gwet_ac1(ratings: list[list]) -> float:
    """Gwet's AC1 for nominal ratings with missing values: units with fewer than two ratings are skipped.

    pa = mean pairwise agreement within units; pe = sum_k pi_k (1 - pi_k) / (K - 1), pi_k the mean share of
    category k, K the number of categories used. Robust to the kappa paradox on skewed binaries.
    """
    units = [[v for v in u if v is not None] for u in ratings]
    units = [u for u in units if len(u) >= 2]
    if not units:
        return float("nan")
    cats = sorted({v for u in units for v in u}, key=str)
    if len(cats) < 2:
        return 1.0
    pa = 0.0
    pi = dict.fromkeys(cats, 0.0)
    for u in units:
        n = len(u)
        c = Counter(u)
        pa += sum(k * (k - 1) for k in c.values()) / (n * (n - 1))
        for k, v in c.items():
            pi[k] += v / n
    pa /= len(units)
    pe = sum((p / len(units)) * (1 - p / len(units)) for p in pi.values()) / (len(cats) - 1)
    return float((pa - pe) / (1 - pe)) if pe < 1 else 1.0


def _text(rec: dict | None, task: str):  # noqa: ANN202
    t = rec.get("text") if rec else None
    return t.get(task) if t else None


def class_table(labels: list, classes: list[str]) -> dict:
    """Consensus class counts with Wilson intervals and the insufficient-data flag."""
    n_with = sum(x is not None for x in labels)
    c = Counter(x for x in labels if x is not None)
    return {
        "n_items": len(labels),
        "no_consensus": len(labels) - n_with,
        "consensus_fraction": round(n_with / len(labels), 4) if labels else None,
        "consensus_fraction_wilson95": wilson(n_with, len(labels)),
        "classes": {
            k: {
                "n": c.get(k, 0),
                "share_of_consensus": round(c.get(k, 0) / n_with, 4) if n_with else None,
                "wilson95": wilson(c.get(k, 0), n_with),
                "insufficient_data": c.get(k, 0) < MIN_POSITIVES,
            }
            for k in classes
        },
    }


def consistency(items: dict[str, dict], ids: list[str]) -> dict:
    """Two-judge rule (llama + qwen + mistral tiebreak) against four-judge consensus, per task. Non-independent."""
    out: dict = {
        "caveat": "not independent: the two-judge labels are built from a subset of the four-judge votes",
        "n_items": len(ids),
    }
    for task in TJ.TASKS:
        agree = both = only_two = only_four = neither = 0
        for i in ids:
            recs = items[i]
            two = TJ.label_item(recs.get(PANEL[0]), recs.get(PANEL[1]), recs.get(PANEL[3]))[task][
                "label"
            ]
            four = consensus_label([_text(recs.get(j), task) for j in PANEL])
            if two is not None and four is not None:
                both += 1
                agree += two == four
            elif two is not None:
                only_two += 1
            elif four is not None:
                only_four += 1
            else:
                neither += 1
        out[task] = {
            "both_labelled": both,
            "agree": agree,
            "agreement": round(agree / both, 4) if both else None,
            "agreement_wilson95": wilson(agree, both),
            "two_judge_only": only_two,
            "four_judge_only": only_four,
            "neither": neither,
        }
    return out


def _nan_to_none(x):  # noqa: ANN001, ANN202
    """JSON has no NaN: an undefined statistic (an empty denominator) is written as null."""
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: _nan_to_none(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_nan_to_none(v) for v in x]
    return x


def sealed_ids(manifest: dict) -> list[str]:
    return sorted(r["id"] for r in manifest["items"] if r["split"] == "test")


def load_items(dirs: list[Path]) -> dict[str, dict[str, dict]]:
    items: dict[str, dict[str, dict]] = {}
    for d in dirs:
        _, part = A.load_judges(d)
        for i, recs in part.items():
            items.setdefault(i, {}).update(recs)
    return items


def build_report(manifest: dict, items: dict[str, dict[str, dict]]) -> dict:
    ids = sealed_ids(manifest)
    missing = [i for i in ids if i not in items]
    if missing:
        raise ValueError(f"{len(missing)} sealed ids have no judge record at all")
    sub = {i: items[i] for i in ids}
    judges = [j for j in PANEL if any(j in r for r in sub.values())]
    if len(judges) < 4:
        raise ValueError(f"sealed set needs all four judges, found {judges}")
    tasks = A.build_tasks(judges, sub, "v3")
    rep: dict = {"n_sealed": len(ids), "judges": judges, "tasks": {}}
    for task, key in GATE_KEYS.items():
        t = dict(tasks[key])
        t["gate"] = A.verdict(t)
        ratings = [[_text(sub[i].get(j), task) for j in judges] for i in ids]
        t["supplementary"] = {
            "gwet_ac1": round(gwet_ac1(ratings), 4),
            "raw_percent_agreement": round(100 * t["pairwise_agreement"], 2),
            "note": "supplementary only; the gate uses alpha and the clear-consensus fraction",
        }
        classes = sorted({v for r in ratings for v in r if v is not None}, key=str)
        labels = [consensus_label(r) for r in ratings]
        t["consensus"] = class_table(labels, classes)
        rep["tasks"][key] = t
    for key, val in tasks.items():
        if key.startswith("T6_"):
            rep["tasks"][key] = {**val, "gate": A.verdict(val)}
    rep["consistency_two_judge_vs_four_judge"] = consistency(sub, ids)
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--judges", nargs="+", required=True, help="merged judge directories (A, S, B)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    rep = build_report(manifest, load_items([Path(d) for d in a.judges]))
    Path(a.out).write_text(
        json.dumps(_nan_to_none(rep), indent=1, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
