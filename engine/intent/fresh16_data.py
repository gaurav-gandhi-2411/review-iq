"""Amendment 16 labels and run-4 training data. Ids and labels only; review text comes from private files.

    python -m engine.intent.fresh16_data labels --manifest reports/labelling/fresh16/manifest.json --judges <panel out/judges> \
        --out reports/intent/labels_fresh16/fresh16_labels.json     # same for train16

`build_labels`: the four-judge consensus of Amendment 5 (`labels._labels_for(stratum, "test", recs)`, at least 3 of 4 per task, aspects quoted by
2+ judges) for every item of a manifest. The same rule labels the sealed gold (`fresh16`) and the training additions (`train16`): the panel
is the labeller of both, as registered. Items without any judge record are an ERROR (a partial panel run is not silently accepted).

`add_train16`: appends the T-train items (split `train16`) of the training manifest to the train split of an `engine.intent.train` data dict and
returns the T-val items (split `val16`) separately, to be used ONLY for choosing the input-length arm. It REFUSES any item whose content hash is on
a sealed list, in the base data under another id, or whose private text/label is missing.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from engine.intent import labels as L
from engine.labelling.scale_report import load_items


def build_labels(manifest: dict, items: dict[str, dict[str, dict]]) -> dict:
    """-> {"classes": {...}, "labels": {task: {id: label}}} for every item of the manifest."""
    tables: dict[str, dict[str, str]] = {t: {} for t in L.task_classes()}
    missing = [r["id"] for r in manifest["items"] if not items.get(r["id"])]
    if missing:
        raise ValueError(f"{len(missing)} manifest ids have no judge record (first: {missing[0]})")
    for r in manifest["items"]:
        for task, lab in L._labels_for(r["category"], "test", items[r["id"]]).items():  # noqa: SLF001 (the registered rule)
            tables[task][r["id"]] = lab
    return {"classes": {t: list(c) for t, c in L.task_classes().items()}, "labels": tables}


def add_train16(
    data: dict,
    texts: dict[str, str],
    manifest: dict,
    labels16: dict,
    text_rows: list[dict],
    sealed_hashes: set[str],
) -> tuple[dict, dict[str, str], dict]:
    """-> (new data with T-train appended to train, new texts, {"val_targeted": {...}, "info": {...}}). Inputs are not modified."""
    classes = data["classes"]
    text_of = {r["id"]: r for r in text_rows}
    new, new_texts = copy.deepcopy(data), dict(texts)
    existing = {i for s in new["ids"].values() for i in s}
    val_t: dict = {"ids": [], "labels": {t: {} for t in classes}, "texts": {}}
    n_train = 0
    for r in manifest["items"]:
        i, sha, split = r["id"], r["sha"], r["split"]
        if split not in ("train16", "val16"):
            raise ValueError(f"{i}: unexpected split {split!r} in the training-additions manifest")
        if sha in sealed_hashes:
            raise ValueError(f"training addition {i} has a sealed hash {sha}")
        if i in existing:
            raise ValueError(f"training addition id {i} already present")
        if i not in text_of or text_of[i]["sha"] != sha:
            raise ValueError(
                f"training addition {i}: private text missing or hash differs from the manifest"
            )
        lab = {t: labels16["labels"][t].get(i) for t in classes}
        for t, v in lab.items():
            if v is not None and v not in classes[t]:
                raise ValueError(f"{i}: label {v!r} is not a class of {t}")
        if split == "train16":
            new["ids"]["train"].append(i)
            new_texts[i] = text_of[i]["text"]
            for t, v in lab.items():
                if v is not None:
                    new["labels"]["train"][t][i] = v
            n_train += 1
        else:
            val_t["ids"].append(i)
            val_t["texts"][i] = text_of[i]["text"]
            for t, v in lab.items():
                if v is not None:
                    val_t["labels"][t][i] = v
    info = {
        "n_added_train": n_train,
        "n_val_targeted": len(val_t["ids"]),
        "n_gold_yes_val_targeted": sum(
            v == "yes" for v in val_t["labels"]["needs_action"].values()
        ),
        "label_basis": "four-judge consensus (Amendments 5 and 16)",
    }
    return new, new_texts, {"val_targeted": val_t, "info": info}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["labels"])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--judges", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    res = build_labels(manifest, load_items([Path(d) for d in a.judges]))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {a.out}: {len(manifest['items'])} items, needs_action labelled {len(res['labels']['needs_action'])}"
    )


if __name__ == "__main__":
    main()
