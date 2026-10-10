"""Fill the kernel template and push it as a PRIVATE Kaggle kernel.

Usage: python -m engine.kaggle.push --mode smoke --branch feat/s21-engine-serving
Then: kaggle kernels status gauravgandhi2411/engine-<mode>; kaggle kernels output <id> -p out/
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

OWNER = "gauravgandhi2411"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["smoke", "final_a", "final_b", "final_c"], required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--no-gpu", action="store_true")
    a = ap.parse_args()
    src = (Path(__file__).parent / "kernel_template.py").read_text(encoding="utf-8")
    slug = f"engine-{a.mode.replace('_', '-')}"
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "kernel.py").write_text(
            src.replace("__MODE__", a.mode).replace("__BRANCH__", a.branch), encoding="utf-8"
        )
        meta = {
            "id": f"{OWNER}/{slug}",
            "title": slug,
            "code_file": "kernel.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": not a.no_gpu,
            "enable_internet": True,
            "dataset_sources": [],
            "competition_sources": [],
            "kernel_sources": [],
        }
        (Path(d) / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        subprocess.run(["kaggle", "kernels", "push", "-p", d], check=True)


if __name__ == "__main__":
    main()
