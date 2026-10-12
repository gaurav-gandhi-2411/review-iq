"""Per-task label tables for the review-intent model (spec Amendment 5). Ids and labels only, never review text.

    python -m engine.intent.labels --manifest reports/labelling/scale/manifest.json \
        --judges <judges_A> <judges_S> <judges_B> --out-dir <dir>

TRAIN / VALIDATION items: `two_judge.label_item` (llama31 + qwen3, mistral tiebreak). SEALED TEST items:
`two_judge.sealed_label_item` (four-judge `consensus_label`). Items that abstain on a task are left out of THAT task
only. Tasks: the four core tasks, plus one aspect-sentiment task per (category, aspect) for beauty and food using the
Amendment 3/4 aspect lists; an aspect is labelled only where at least two judges quoted it (enforced by the two_judge
functions). Aspect tasks are named `aspect:<category>:<aspect>`.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from engine.labelling import schema as S
from engine.labelling import two_judge as TJ
from engine.labelling.prompts_v3 import ASPECTS_V3, BROAD_INTENTS
from engine.labelling.scale_report import load_items

PANEL = ("llama31_8b", "qwen3_8b", "gemma2_9b", "mistral_7b")
ASPECT_CATEGORIES = ("beauty", "food")  # Amendment 4: apparel aspects dropped, vernacular has none
SPLITS = ("train", "val", "test")

Tables = dict[str, dict[str, dict[str, str]]]  # split -> task -> id -> label


def task_classes() -> dict[str, tuple[str, ...]]:
    """Ordered class list per task (the head's output order)."""
    out: dict[str, tuple[str, ...]] = {
        "needs_action": S.YES_NO,
        "broad_intent": BROAD_INTENTS,
        "sentiment": S.SENTIMENTS,
        "urgency": S.URGENCY,
    }
    for cat in ASPECT_CATEGORIES:
        for asp in ASPECTS_V3[cat]:
            out[f"aspect:{cat}:{asp}"] = S.ASPECT_SENTIMENTS
    return out


def _labels_for(stratum: str, split: str, recs: dict[str, dict]) -> dict[str, str]:
    """{task: label} for one item; abstentions are absent."""
    if split == "test":
        lab = TJ.sealed_label_item([recs.get(j) for j in PANEL])
    else:
        a, b, m = (recs.get(j) for j in (PANEL[0], PANEL[1], PANEL[3]))
        lab = TJ.label_item(a, b, m)
        lab = {t: (lab[t]["label"] if t in TJ.TASKS else lab[t]) for t in lab}
    out = {t: lab[t] for t in TJ.TASKS if lab[t] is not None}
    if stratum in ASPECT_CATEGORIES:
        for asp, sent in lab["aspects"].items():
            if asp in ASPECTS_V3[stratum]:  # a judge answer outside the allowed list is not a label
                out[f"aspect:{stratum}:{asp}"] = sent
    return out


def build_tables(
    manifest: dict, items: dict[str, dict[str, dict]], allow_partial: bool = False
) -> Tables:
    """Label tables per split. A manifest item with no judge record at all raises unless `allow_partial`."""
    tables: Tables = {s: {t: {} for t in task_classes()} for s in SPLITS}
    missing = 0
    for row in manifest["items"]:
        recs = items.get(row["id"])
        if not recs:
            missing += 1
            continue
        for task, lab in _labels_for(row["stratum"], row["split"], recs).items():
            tables[row["split"]][task][row["id"]] = lab
    if missing and not allow_partial:
        raise ValueError(
            f"{missing} manifest ids have no judge record (pass allow_partial to skip)"
        )
    return tables


def summarize(manifest: dict, tables: Tables) -> dict:
    """Counts only: labelled items and class counts per split and task, items in the split."""
    n_split = Counter(r["split"] for r in manifest["items"])
    return {
        s: {
            "n_items": n_split[s],
            "tasks": {
                t: {"n_labelled": len(v), "classes": dict(sorted(Counter(v.values()).items()))}
                for t, v in tables[s].items()
            },
        }
        for s in SPLITS
    }


def load_texts(path: Path) -> dict[str, str]:
    """id -> review text from the private scale.jsonl."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["id"]] = r["text"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--judges", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-partial", action="store_true")
    a = ap.parse_args()
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    tables = build_tables(
        manifest, load_items([Path(d) for d in a.judges]), allow_partial=a.allow_partial
    )
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "labels.json").write_text(
        json.dumps({"classes": task_classes(), "labels": tables}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "labels_summary.json").write_text(
        json.dumps(summarize(manifest, tables), indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
