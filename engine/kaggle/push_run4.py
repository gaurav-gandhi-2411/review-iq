"""Push the Amendment 16 run-4 kernel (train arms, choose on validation, quantise, score) as a PRIVATE one-T4 Kaggle kernel.

    python -m engine.kaggle.push_run4 --dataset gauravgandhi2411/review-intent-run4-in --branch <branch> --slug review-intent-run4 \
        --arms H128:128:head,H256:256:head,HT128:128:head_tail,HT256:256:head_tail [--mode smoke --epochs 1] [--dry-run-dir DIR]

`--mode smoke` trains the first arm for `--epochs` on the run-2 data only and exercises export, the layer-sensitivity rule, quantisation and scoring on a stand-in
(no training additions, no sealed or fresh item); `--mode full` is the registered run. The dataset (private) is described in engine/kaggle/kernel_run4.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def build(branch: str, arms: str, mode: str, epochs: int, patience: int) -> str:
    src = (Path(__file__).parent / "kernel_run4.py").read_text(encoding="utf-8")
    return (
        src.replace("__BRANCH__", branch)
        .replace("__ARMS__", arms)
        .replace("__MODE__", mode)
        .replace("__EPOCHS__", str(epochs))
        .replace("__PATIENCE__", str(patience))
    )


def metadata(slug: str, dataset: str) -> dict:
    return {
        "id": f"gauravgandhi2411/{slug}", "title": slug, "code_file": "kernel.py",
        "language": "python", "kernel_type": "script", "is_private": True,
        "enable_gpu": True, "enable_internet": True, "dataset_sources": [dataset],
        "competition_sources": [], "kernel_sources": [],
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--arms", required=True)
    ap.add_argument("--mode", choices=["smoke", "full"], default="full")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--dry-run-dir", default="")
    a = ap.parse_args()
    if a.slug == a.dataset.split("/")[-1]:
        raise SystemExit("kernel slug must differ from the dataset slug")
    src, meta = build(a.branch, a.arms, a.mode, a.epochs, a.patience), metadata(a.slug, a.dataset)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(a.dry_run_dir) if a.dry_run_dir else Path(tmp)
        d.mkdir(parents=True, exist_ok=True)
        (d / "kernel.py").write_text(src, encoding="utf-8")
        (d / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        if a.dry_run_dir:
            print(f"dry run: wrote {d}; nothing pushed")
            return
        subprocess.run(["kaggle", "kernels", "push", "-p", str(d)], check=True)


if __name__ == "__main__":
    main()
