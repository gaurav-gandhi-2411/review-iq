"""Auxiliary out-of-domain training items (S23): label tables and an include/exclude merge into TRAIN only.

    python -m engine.intent.aux_domain --manifest reports/labelling/fooddelivery/manifest.json \
        --judges <judges_A> <judges_B> --out reports/labelling/fooddelivery/labels.json

Labels follow the Amendment 5 two-judge rule (llama3.1 + qwen3, mistral tiebreak) via `labels._labels_for`.
`merge_aux` adds the items to the TRAIN split of a `train.run` data dict when `include` is true and refuses anything
that is not `domain=out_of_domain_food_delivery, split=aux_train`, or whose id/hash is already in val/test. It never
writes to val or test, so the auxiliary set cannot become a validation or sealed item through this path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.intent.labels import _labels_for
from engine.labelling.fooddelivery_aux import DOMAIN, SPLIT
from engine.labelling.scale_report import load_items


def build_aux_labels(
    manifest: dict, items: dict[str, dict[str, dict]]
) -> dict[str, dict[str, str]]:
    """task -> id -> label for the aux items (abstentions absent). Missing judge records raise."""
    out: dict[str, dict[str, str]] = {}
    missing = [r["id"] for r in manifest["items"] if r["id"] not in items]
    if missing:
        raise ValueError(f"{len(missing)} aux ids have no judge record")
    for row in manifest["items"]:
        for task, lab in _labels_for(row["stratum"], "train", items[row["id"]]).items():
            out.setdefault(task, {})[row["id"]] = lab
    return out


def merge_aux(
    data: dict, texts: dict[str, str], aux_manifest: dict, aux_labels: dict, aux_texts: dict[str, str],
    include: bool,
) -> tuple[dict, dict[str, str]]:  # fmt: skip
    """Return (data, texts) with the aux items added to TRAIN iff `include`; validated either way."""
    rows = aux_manifest["items"]
    bad = [r["id"] for r in rows if r.get("domain") != DOMAIN or r.get("split") != SPLIT]
    if bad:
        raise ValueError(f"{len(bad)} aux items are not domain={DOMAIN} split={SPLIT}")
    ids = {r["id"] for r in rows}
    for s in ("val", "test"):
        clash = ids & set(data["ids"].get(s, []))
        if clash:
            raise ValueError(f"{len(clash)} aux ids appear in the {s} split")
    if not include:
        return data, texts
    if ids & set(data["ids"]["train"]):
        raise ValueError("aux ids collide with existing train ids")
    labels = {s: {t: dict(v) for t, v in d.items()} for s, d in data["labels"].items()}
    for task, tab in aux_labels.items():
        if task in labels["train"]:  # a task the head does not have is dropped, not invented
            labels["train"][task].update(tab)
    ids_out = {s: list(v) for s, v in data["ids"].items()}
    ids_out["train"] += [r["id"] for r in rows]
    return {**data, "labels": labels, "ids": ids_out}, {**texts, **aux_texts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--judges", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    labels = build_aux_labels(man, load_items([Path(d) for d in a.judges]))
    Path(a.out).write_text(
        json.dumps({"domain": DOMAIN, "split": SPLIT, "labels": labels}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
