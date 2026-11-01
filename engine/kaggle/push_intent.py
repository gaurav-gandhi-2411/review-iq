"""Push the review-intent training kernel as a PRIVATE Kaggle kernel. Does not run anything by itself.

    python -m engine.kaggle.push_intent --dataset gauravgandhi2411/review-intent-train --branch <branch> \
        [--slug review-intent-train] [--train-args "--epochs 30 --patience 3 --save-weights"] [--dry-run-dir DIR]

The dataset (private) must hold `scale.jsonl` and `labels.json`. `--dry-run-dir` writes the kernel and metadata
there and does not call kaggle. Then: kaggle kernels status gauravgandhi2411/<slug>; kaggle kernels output <id> -p out/
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from engine.kaggle.push_scale import metadata


def build(branch: str, train_args: str = "") -> str:
    src = (Path(__file__).parent / "kernel_intent.py").read_text(encoding="utf-8")
    return src.replace("__BRANCH__", branch).replace("__TRAIN_ARGS__", train_args)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--slug", default="review-intent-train")
    ap.add_argument("--train-args", default="", help="extra flags for engine.intent.train")
    ap.add_argument("--dry-run-dir", default="")
    a = ap.parse_args()
    src, meta = build(a.branch, a.train_args), metadata(a.slug, [a.dataset])
    if a.dry_run_dir:
        d = Path(a.dry_run_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "kernel.py").write_text(src, encoding="utf-8")
        (d / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print(f"dry run: wrote {d}; nothing pushed")
        return
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "kernel.py").write_text(src, encoding="utf-8")
        (Path(d) / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        subprocess.run(["kaggle", "kernels", "push", "-p", d], check=True)


if __name__ == "__main__":
    main()
