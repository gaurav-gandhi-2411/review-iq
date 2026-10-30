"""Push the ONNX numerics-equivalence kernel (S30) as a PRIVATE CPU Kaggle kernel; the kernel slug must differ from the dataset slug.

    python -m engine.kaggle.push_onnx_equiv --dataset gauravgandhi2411/review-intent-onnx-equiv --slug review-intent-onnx-equiv-run \
        --branch feat/s30-pin-ort-numerics
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def build(branch: str) -> str:
    return (
        (Path(__file__).parent / "kernel_onnx_equiv.py")
        .read_text(encoding="utf-8")
        .replace("__BRANCH__", branch)
    )


def metadata(slug: str, dataset: str) -> dict:
    return {
        "id": f"gauravgandhi2411/{slug}", "title": slug, "code_file": "kernel.py",
        "language": "python", "kernel_type": "script", "is_private": True,
        "enable_gpu": False, "enable_internet": True, "dataset_sources": [dataset],
        "competition_sources": [], "kernel_sources": [],
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--branch", default="main")
    a = ap.parse_args()
    if a.slug == a.dataset.split("/")[-1]:
        raise SystemExit("kernel slug must differ from the dataset slug")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "kernel.py").write_text(build(a.branch), encoding="utf-8")
        (Path(d) / "kernel-metadata.json").write_text(
            json.dumps(metadata(a.slug, a.dataset), indent=1), encoding="utf-8"
        )
        subprocess.run(["kaggle", "kernels", "push", "-p", d], check=True)


if __name__ == "__main__":
    main()
