"""Push the Ollama judge-panel kernel as a PRIVATE Kaggle kernel.

    python -m engine.kaggle.push_ollama --items pilot1.jsonl --dataset gauravgandhi2411/review-intent-pilot1 \
        --branch main --slug review-intent-judges-pilot1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--slug", required=True)
    a = ap.parse_args()
    src = (Path(__file__).parent / "kernel_ollama.py").read_text(encoding="utf-8")
    src = src.replace("__BRANCH__", a.branch).replace("__ITEMS__", a.items)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "kernel.py").write_text(src, encoding="utf-8")
        meta = {
            "id": f"gauravgandhi2411/{a.slug}", "title": a.slug, "code_file": "kernel.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": True, "enable_internet": True, "dataset_sources": [a.dataset],
            "competition_sources": [], "kernel_sources": [],
        }  # fmt: skip
        (Path(d) / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        subprocess.run(["kaggle", "kernels", "push", "-p", d], check=True)


if __name__ == "__main__":
    main()
