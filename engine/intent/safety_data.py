"""Safety-stratum TRAIN items for run 3 (spec Amendment 7). Ids, hashes and labels only; text comes from a private file.

`add_safety_train` appends the stratum's TRAIN items (with their four-judge panel labels) to the train split of an
`engine.intent.train` data dict. It REFUSES, with a ValueError, to use any item that is in the safety val/test/dropped
lists, whose content hash is on a sealed list (base test or safety test), or whose id/hash collides with an existing item,
so a leak aborts the run instead of silently training on a sealed item.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

TASKS = ("needs_action", "broad_intent", "urgency")  # panel-labelled; sentiment and aspects stay masked


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def safety_split(split_json: dict, split: str) -> list[dict]:
    return [r for r in split_json["items"] if r["split"] == split]


def add_safety_train(
    data: dict, texts: dict[str, str], split_json: dict, label_rows: list[dict],
    text_rows: list[dict], sealed_hashes: set[str],
) -> tuple[dict, dict[str, str], dict]:  # fmt: skip
    """-> (new data, new texts, info). Inputs are not modified.

    sealed_hashes = every hash on a sealed list (`leakage_ledger.sealed_hashes()`: base test plus safety test)."""
    train = safety_split(split_json, "train")
    held_out = {r["id"] for s in ("val", "test", "dropped") for r in safety_split(split_json, s)}
    held_out_sha = {r["sha"] for s in ("val", "test") for r in safety_split(split_json, s)}
    labels = {r["id"]: r for r in label_rows}
    text_of = {r["id"]: r for r in text_rows}
    new, new_texts = copy.deepcopy(data), dict(texts)
    existing = {i for s in new["ids"].values() for i in s}
    n_pos = 0
    for r in train:
        i, sha = r["id"], r["sha"]
        if i in held_out or sha in held_out_sha:
            raise ValueError(f"safety train item {i} is also in a val/test/dropped list")
        if sha in sealed_hashes:
            raise ValueError(f"safety train item {i} has a sealed hash {sha}")
        if i in existing:
            raise ValueError(f"safety id {i} already present in the base data")
        if i not in text_of or text_of[i]["sha"] != sha:
            raise ValueError(f"safety item {i}: private text missing or hash differs from split.json")
        if i not in labels or labels[i]["sha"] != sha:
            raise ValueError(f"safety item {i}: panel label missing or hash differs from split.json")
        new["ids"]["train"].append(i)
        new_texts[i] = text_of[i]["text"]
        for t in TASKS:
            lab = labels[i].get(t)
            if lab is None:  # panel abstention: left out of THIS task only
                continue
            if lab not in new["classes"][t]:
                raise ValueError(f"safety item {i}: label {lab!r} not a class of {t}")
            new["labels"]["train"][t][i] = lab
        n_pos += labels[i].get("urgency") == "high"
    info = {
        "n_added_train": len(train), "n_urgency_high": n_pos,
        "panel_label_basis": "four-judge consensus (Amendment 6)", "tasks_labelled": list(TASKS),
    }  # fmt: skip
    return new, new_texts, info
