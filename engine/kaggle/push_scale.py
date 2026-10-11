"""Push one stage of the scale-labelling kernel as a PRIVATE Kaggle kernel. Does not run anything by itself.

    python -m engine.kaggle.push_scale --stage A --items scale_A.jsonl --dataset gauravgandhi2411/review-intent-scale-a \
        [--prior gauravgandhi2411/review-intent-scale-a-out] --branch main --slug review-intent-scale-a [--dry-run]

`--prior` is a private dataset holding the previous session's out/judges (ids and labels only) so a later session
resumes instead of redoing work. `--dry-run` writes the kernel and metadata to --dry-run-dir and does not call kaggle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def build(stage: str, items: str, branch: str, budget_h: float) -> str:
    src = (Path(__file__).parent / "kernel_scale.py").read_text(encoding="utf-8")
    return (
        src.replace("__BRANCH__", branch)
        .replace("__STAGE__", stage)
        .replace("__ITEMS__", items)
        .replace("__BUDGET_H__", str(budget_h))
    )


def metadata(slug: str, datasets: list[str]) -> dict:
    return {
        "id": f"gauravgandhi2411/{slug}", "title": slug, "code_file": "kernel.py",
        "language": "python", "kernel_type": "script", "is_private": True,
        "enable_gpu": True, "enable_internet": True, "dataset_sources": datasets,
        "competition_sources": [], "kernel_sources": [],
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["A", "S", "B"])
    ap.add_argument("--items", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--prior", default="")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--slug", required=True)
    ap.add_argument(
        "--budget-hours",
        type=float,
        default=8.0,
        help="stop starting chunks after this; 9h session limit",
    )
    ap.add_argument("--dry-run-dir", default="")
    a = ap.parse_args()
    datasets = [a.dataset] + ([a.prior] if a.prior else [])
    src = build(a.stage, a.items, a.branch, a.budget_hours)
    meta = metadata(a.slug, datasets)
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
