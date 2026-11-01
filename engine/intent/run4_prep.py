"""Amendment 16: the two small text files the quantiser needs, built from the registered inputs.

    PYTHONPATH=. python -m engine.intent.run4_prep --labels labels.json --scale-texts scale.jsonl --train16-manifest reports/labelling/train16/manifest.json \
        --train16-texts train16.jsonl --arm-dir <fp32 arm dir> --out-dir <dir>      # writes calib.jsonl and val.jsonl

calib.jsonl = the TRAINING pool the 256 calibration texts are sampled from: every run-2 training item (a label on any task) plus the T-train additions (split train16).
Never a validation, T-val, test, sealed or fresh item. val.jsonl = the original validation items in the arm's pred-file order (what the layer-sensitivity rule scores).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_rows(path: Path) -> dict[str, str]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["id"]: r["text"] for r in rows}


def calibration_pool(
    labels: dict, scale: dict[str, str], t_manifest: dict | None, t_texts: dict[str, str] | None
) -> list[dict]:
    train = labels["labels"]["train"]
    ids = sorted(set().union(*[set(v) for v in train.values()]))
    pool = [{"id": i, "text": scale[i]} for i in ids]
    if (
        t_manifest is not None and t_texts is not None
    ):  # None: the smoke run, run-2 training data only
        pool += [
            {"id": r["id"], "text": t_texts[r["id"]]}
            for r in t_manifest["items"]
            if r["split"] == "train16"
        ]
    held = set().union(*[set(v) for s in ("val", "test") for v in labels["labels"][s].values()])
    clash = held & {r["id"] for r in pool}
    if clash:
        raise ValueError(
            f"{len(clash)} calibration items are validation/test items (first: {sorted(clash)[0]})"
        )
    return pool


def val_rows(arm_dir: Path, scale: dict[str, str]) -> list[dict]:
    ids = json.loads((arm_dir / "pred_needs_action.json").read_text(encoding="utf-8"))["splits"][
        "val"
    ]["ids"]
    return [{"id": i, "text": scale[i]} for i in ids]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--scale-texts", type=Path, required=True)
    ap.add_argument("--train16-manifest", type=Path)
    ap.add_argument("--train16-texts", type=Path)
    ap.add_argument("--arm-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    labels = json.loads(a.labels.read_text(encoding="utf-8"))
    scale = read_rows(a.scale_texts)
    pool = calibration_pool(
        labels,
        scale,
        json.loads(a.train16_manifest.read_text(encoding="utf-8")) if a.train16_manifest else None,
        read_rows(a.train16_texts) if a.train16_texts else None,
    )
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("calib", pool), ("val", val_rows(a.arm_dir, scale))):
        (a.out_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
    print(f"calibration pool {len(pool)} items, validation {len(val_rows(a.arm_dir, scale))} items")


if __name__ == "__main__":
    main()
