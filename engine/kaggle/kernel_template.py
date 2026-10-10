"""Kaggle kernel body for the E2 sweep. Filled in and pushed by engine/kaggle/push.py.

No secret is ever put in a kernel: the Groq LLM baseline is NOT run here (it runs locally).
The repo is public, so the clone needs no credentials. Results are JSON files under
/kaggle/working/out and are pulled with `kaggle kernels output`.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

MODE = "__MODE__"
BRANCH = "__BRANCH__"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
MINI = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
XLMR = "xlm-roberta-base"
E5 = "intfloat/multilingual-e5-base"
LOCALES8 = "en-US,hi-IN,ta-IN,bn-BD,de-DE,ja-JP,ar-SA,sw-KE"

os.environ.update(
    HF_HOME=str(W / "cache/hf"),
    HF_DATASETS_CACHE=str(W / "cache/hfd"),
    TORCH_HOME=str(W / "cache/torch"),
    ENGINE_DATA_DIR=str(W / "engine-data"),
    PYTHONIOENCODING="utf-8",
    TOKENIZERS_PARALLELISM="false",
)

# (name, model, extra args)
JOBS = {
    "smoke": [("smoke_clinc_A", MINI, "--dataset clinc --epochs 1")],
    "models": [
        (f"{tag}_{ds}_A", m, args)
        for tag, m in (("minilm", MINI), ("xlmr", XLMR), ("e5", E5))
        for ds, args in (
            ("clinc", "--dataset clinc --epochs 6"),
            ("massive_en", "--dataset massive --locales en-US --epochs 6"),
        )
    ],
    "sweep_minilm": [
        ("minilm_banking77_A_s42", MINI, "--dataset banking77 --epochs 8"),
        (
            "minilm_massive_A_8loc",
            MINI,
            f"--dataset massive --locales {LOCALES8} --epochs 3 --batch-size 64",
        ),
        ("minilm_clinc_B_h30", MINI, "--dataset clinc --holdout 30 --unknown-head --epochs 6"),
        (
            "minilm_banking77_B_h20",
            MINI,
            "--dataset banking77 --holdout 20 --unknown-head --epochs 8",
        ),
        (
            "minilm_massive_B_h15",
            MINI,
            "--dataset massive --locales en-US --holdout 15 --unknown-head --epochs 6",
        ),
    ]
    + [
        (f"minilm_{ds}_A_s{s}", MINI, f"--dataset {ds} --seed {s} --epochs {e}")
        for s in (43, 44)
        for ds, e in (("clinc", 6), ("banking77", 8))
    ],
}[MODE]


def sh(cmd, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


t_all = time.time()
gpu = sh(
    "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", capture_output=True, text=True
)
print("GPU:", gpu.stdout.strip(), flush=True)
repo = W / "review-iq"
if repo.exists():
    shutil.rmtree(repo)
sh(f"git clone --depth 1 --branch {BRANCH} {REPO} {repo}", check=True)
sha = subprocess.run(
    f"git -C {repo} rev-parse HEAD", shell=True, capture_output=True, text=True
).stdout.strip()
os.chdir(repo)
sys.path.insert(0, str(repo))
sh(f"{sys.executable} -m engine.fetch_data", check=True)
out = W / "out"
out.mkdir(exist_ok=True)
timings = {"gpu": gpu.stdout.strip(), "commit": sha, "mode": MODE, "jobs": {}}
for name, model, args in JOBS:
    t0 = time.time()
    r = sh(
        f"{sys.executable} -u -m engine.experiments.run --model {model} --out {out}/{name}.json {args}",
        capture_output=True,
        text=True,
    )
    (out / f"{name}.log").write_text(
        (r.stdout or "")[-20000:] + (r.stderr or "")[-8000:], encoding="utf-8"
    )
    timings["jobs"][name] = {"rc": r.returncode, "seconds": round(time.time() - t0, 1)}
    print(name, timings["jobs"][name], flush=True)
    (out / "timings.json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
timings["total_seconds"] = round(time.time() - t_all, 1)
(out / "timings.json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
shutil.rmtree(repo, ignore_errors=True)  # keep /kaggle/working to the small outputs only
shutil.rmtree(W / "cache", ignore_errors=True)
shutil.rmtree(W / "engine-data", ignore_errors=True)
print("done", timings["total_seconds"], flush=True)
