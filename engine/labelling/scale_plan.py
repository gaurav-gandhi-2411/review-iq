"""Run plan for scale labelling (spec Amendment 5): stages, item selection, resumable chunks, merge, GPU estimate.

    python -m engine.labelling.scale_plan items  --scale D:/ml-cache/review-intent/scale.jsonl --stage A --out D:/ml-cache/s22/ds_scale_A/scale_A.jsonl
    python -m engine.labelling.scale_plan stage-b --scale ... --judges reports/labelling/scale/judges_A \
        --ids-out reports/labelling/scale/stageB_ids.json --items-out D:/ml-cache/s22/ds_scale_B/scale_B.jsonl
    python -m engine.labelling.scale_plan merge --src DOWNLOADED_OUT/judges --dst reports/labelling/scale/judges_A
    python -m engine.labelling.scale_plan estimate

Stages: A = llama3.1 + qwen3 on ALL items, sharded across the two T4s; S = gemma2 (GPU 0) and mistral (GPU 1) on the
SEALED items; B = mistral on the training/validation items where `two_judge.needs_tiebreak` is true, sharded.
Everything written to the repository is ids and labels (the Stage B id list, judge files); review text goes only to
private files / private Kaggle datasets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.labelling import two_judge as TJ

JUDGES = {
    "llama31_8b": "llama3.1:8b",
    "qwen3_8b": "qwen3:8b",
    "gemma2_9b": "gemma2:9b",
    "mistral_7b": "mistral:7b",
}
# stage -> (per-GPU judge list, sharded across GPUs?)
STAGES: dict[str, tuple[dict[int, list[str]], bool]] = {
    "A": ({0: ["llama31_8b", "qwen3_8b"], 1: ["llama31_8b", "qwen3_8b"]}, True),
    "S": ({0: ["gemma2_9b"], 1: ["mistral_7b"]}, False),
    "B": ({0: ["mistral_7b"], 1: ["mistral_7b"]}, True),
}
# measured, reports/labelling/pilot3/kernel_status.json (seconds per item, one T4, 360 items)
SEC_PER_ITEM = {"llama31_8b": 3.292, "gemma2_9b": 4.392, "qwen3_8b": 2.783, "mistral_7b": 3.939}
CHUNK = 200  # items per judge call batch: bounds what a killed session can lose


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def select_items(items: list[dict], stage: str) -> list[dict]:
    """Stage A: all items; S: the sealed test split; B is built by compute_stage_b."""
    if stage == "A":
        return list(items)
    if stage == "S":
        return [r for r in items if r["split"] == "test"]
    raise ValueError("stage B items come from compute_stage_b")


def shard_of(index: int, n_shards: int = 2) -> int:
    return index % n_shards


def pending_chunks(items: list[dict], done: set[str], size: int = CHUNK) -> list[list[dict]]:
    """Items not yet labelled, in file order, cut into chunks."""
    todo = [r for r in items if r["id"] not in done]
    return [todo[i : i + size] for i in range(0, len(todo), size)]


def done_ids(path: Path) -> set[str]:
    return {r["id"] for r in read_jsonl(path)} if path.exists() else set()


def compute_stage_b(items: list[dict], judges_dir: Path) -> list[str]:
    """Training/validation ids needing mistral, from the merged stage A outputs. Raises if A is incomplete."""
    a = {r["id"]: r for r in read_jsonl(judges_dir / "llama31_8b.jsonl")}
    b = {r["id"]: r for r in read_jsonl(judges_dir / "qwen3_8b.jsonl")}
    ids = [r["id"] for r in items if r["split"] != "test"]
    missing = [i for i in ids if i not in a or i not in b]
    if missing:
        raise ValueError(f"stage A incomplete: {len(missing)} training/val ids lack a judge record")
    return TJ.tiebreak_ids(a, b, ids)


def merge_shards(src: Path, dst: Path) -> dict[str, int]:
    """Merge <judge>__shard<k>.jsonl (and plain <judge>.jsonl) from src into dst/<judge>.jsonl, one line per id."""
    dst.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict[str, dict]] = {}
    for f in sorted(src.glob("*.jsonl")):
        name = f.stem.split("__shard")[0]
        for r in read_jsonl(f):
            r["judge"] = name
            out.setdefault(name, {})[r["id"]] = (
                r  # later shard/duplicate overwrites: judge runs are deterministic
            )
    for name, recs in out.items():
        (dst / f"{name}.jsonl").write_text(
            "".join(json.dumps(recs[i], ensure_ascii=False) + "\n" for i in sorted(recs)),
            encoding="utf-8",
        )
    return {k: len(v) for k, v in out.items()}


def estimate(n_total: int, n_sealed: int, mistral_rate: float, n_gpu: int = 2) -> dict:
    """GPU-seconds per stage from measured seconds/item; wall-clock if the GPUs are balanced."""
    n_train = n_total - n_sealed
    s = SEC_PER_ITEM
    stage = {
        "A": n_total * (s["llama31_8b"] + s["qwen3_8b"]),
        "S": n_sealed * (s["gemma2_9b"] + s["mistral_7b"]),
        "B": n_train * mistral_rate * s["mistral_7b"],
    }
    total = sum(stage.values())
    return {
        "inputs": {"n_total": n_total, "n_sealed": n_sealed, "mistral_rate": mistral_rate},
        "gpu_hours_by_stage": {k: round(v / 3600, 2) for k, v in stage.items()},
        "gpu_hours_total": round(total / 3600, 2),
        "wall_hours_balanced_on_gpus": round(total / 3600 / n_gpu, 2),
        "note": "excludes model pulls and server start (about 10 minutes per session)",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("items")
    p.add_argument("--scale", required=True)
    p.add_argument("--stage", choices=["A", "S"], required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("stage-b")
    p.add_argument("--scale", required=True)
    p.add_argument("--judges", required=True)
    p.add_argument("--ids-out", required=True)
    p.add_argument("--items-out", required=True)
    p = sub.add_parser("merge")
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    p = sub.add_parser("estimate")
    p.add_argument("--n-total", type=int, default=5000)
    p.add_argument("--n-sealed", type=int, default=763)
    p.add_argument("--mistral-rate", type=float, default=0.30)
    a = ap.parse_args()

    if a.cmd == "items":
        rows = select_items(read_jsonl(Path(a.scale)), a.stage)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        print(f"{len(rows)} items -> {a.out}")
    elif a.cmd == "stage-b":
        items = read_jsonl(Path(a.scale))
        ids = compute_stage_b(items, Path(a.judges))
        keep = set(ids)
        Path(a.ids_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.ids_out).write_text(
            json.dumps({"stage": "B", "n": len(ids), "ids": ids}, indent=1) + "\n", encoding="utf-8"
        )
        Path(a.items_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.items_out).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in items if r["id"] in keep),
            encoding="utf-8",
        )
        print(f"{len(ids)} stage B items (ids -> {a.ids_out}; private text -> {a.items_out})")
    elif a.cmd == "merge":
        print(json.dumps(merge_shards(Path(a.src), Path(a.dst))))
    else:
        print(json.dumps(estimate(a.n_total, a.n_sealed, a.mistral_rate), indent=1))


if __name__ == "__main__":
    main()
