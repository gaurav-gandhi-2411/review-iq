"""Kaggle kernel body: train the multi-task review-intent model (engine.intent.train) on one T4.

Filled in by engine/kaggle/push_intent.py (__BRANCH__). Reads `scale.jsonl` (review text, licensed, private) and
`labels.json` (ids and labels only, from engine.intent.labels) from a PRIVATE Kaggle dataset; the manifest comes from
the repository checkout. Output: /kaggle/working/out/{pred_*.json, metadata.json, train.log}. No secret enters the kernel.
"""

import glob
import os
import pathlib
import shutil
import subprocess
import sys

BRANCH = "__BRANCH__"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out"

os.environ.update(
    HF_HOME=str(W / "cache/hf"),
    TORCH_HOME=str(W / "cache/torch"),
    PYTHONIOENCODING="utf-8",
    TOKENIZERS_PARALLELISM="false",
)


def sh(cmd, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


def find(name):
    hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
    if not hits:
        raise SystemExit(f"{name} not found in the attached dataset")
    return hits[0]


repo = W / "review-iq"
if repo.exists():
    shutil.rmtree(repo)
sh(f"git clone --depth 1 --branch {BRANCH} {REPO} {repo}", check=True)
os.chdir(repo)
OUT.mkdir(exist_ok=True)
r = sh(
    f"{sys.executable} -u -m engine.intent.train --labels {find('labels.json')} "
    f"--texts {find('scale.jsonl')} --manifest reports/labelling/scale/manifest.json "
    f"--out-dir {OUT} --device cuda",
    capture_output=True,
    text=True,
)
(OUT / "train.log").write_text(
    (r.stdout or "")[-20000:] + (r.stderr or "")[-8000:], encoding="utf-8"
)
print((r.stdout or "")[-3000:], flush=True)
shutil.rmtree(repo, ignore_errors=True)  # keep /kaggle/working to the outputs only
shutil.rmtree(W / "cache", ignore_errors=True)
if r.returncode:
    raise SystemExit(r.returncode)
