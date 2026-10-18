"""Push the gpt-oss-20b-local moat kernel (spec Amendment 9) as a PRIVATE Kaggle kernel; the kernel slug must differ from the dataset slug.

    python -m engine.kaggle.push_moat20b --mode smoke --dataset gauravgandhi2411/review-intent-moat-items --slug review-intent-moat-run \
        --branch feat/s23-moat-20b-local [--prior gauravgandhi2411/review-intent-moat-prior] [--budget-hours 8]

`--arm fewshot` (spec Amendment 10) additionally needs a private `shots.jsonl` next to `items.jsonl` in the dataset.
`--mode smoke` runs the first 20 sealed items only (throughput and JSON-mode check; rows discarded). `--prior` is a private dataset with the
previous session's `shards/` folder (ids, answers, token counts; no text).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def build(
    branch: str, mode: str, budget_h: float, arm: str = "zeroshot", no_json_mode: bool = False,
    max_tokens: int = 600, effort: str = "none",
) -> str:  # fmt: skip
    src = (Path(__file__).parent / "kernel_moat20b.py").read_text(encoding="utf-8")
    return (
        src.replace("__BRANCH__", branch)
        .replace("__MODE__", mode)
        .replace("__BUDGET_H__", str(budget_h))
        .replace("__ARM__", arm)
        .replace("__NOJSON__", "1" if no_json_mode else "0")
        .replace("__MAXTOK__", str(max_tokens))
        .replace("__EFFORT__", effort)
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
    ap.add_argument("--mode", required=True, choices=["smoke", "full", "canary", "effort"])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--prior", default="")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--budget-hours", type=float, default=8.0)
    ap.add_argument("--arm", choices=["zeroshot", "fewshot", "both"], default="zeroshot")
    ap.add_argument("--no-json-mode", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--effort", choices=["none", "low", "medium", "high"], default="none")
    a = ap.parse_args()
    if a.slug == a.dataset.split("/")[-1]:
        raise SystemExit("kernel slug must differ from the dataset slug")
    datasets = [a.dataset] + ([a.prior] if a.prior else [])
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "kernel.py").write_text(
            build(a.branch, a.mode, a.budget_hours, a.arm, a.no_json_mode, a.max_tokens, a.effort),
            encoding="utf-8",
        )
        meta = metadata(a.slug, datasets)
        (Path(d) / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        subprocess.run(["kaggle", "kernels", "push", "-p", d], check=True)


if __name__ == "__main__":
    main()
