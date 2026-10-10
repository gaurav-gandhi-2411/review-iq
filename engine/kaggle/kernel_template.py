"""Kaggle kernel body for the E2 runs. Filled in and pushed by engine/kaggle/push.py.

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
OUT = W / "out"
MINI = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
XLMR = "xlm-roberta-base"
E5 = "intfloat/multilingual-e5-base"
LOC8 = "en-US,hi-IN,ta-IN,bn-BD,de-DE,ja-JP,ar-SA,sw-KE"
RUN, HIER, SMALL = "run", "hierarchy", "small_data"

os.environ.update(
    HF_HOME=str(W / "cache/hf"),
    HF_DATASETS_CACHE=str(W / "cache/hfd"),
    TORCH_HOME=str(W / "cache/torch"),
    ENGINE_DATA_DIR=str(W / "engine-data"),
    PYTHONIOENCODING="utf-8",
    TOKENIZERS_PARALLELISM="false",
)

DS = {  # dataset -> (epochs, base args)
    "clinc": (6, "--dataset clinc"),
    "banking77": (8, "--dataset banking77"),
    "massive_en": (6, "--dataset massive --locales en-US"),
}


def track_a(tag, model, seeds, datasets=("clinc", "banking77", "massive_en"), export=()):
    jobs = []
    for ds in datasets:
        ep, base = DS[ds]
        for s in seeds:
            extra = ""
            if s == 42 and ds in export:
                extra = f" --export-dir {OUT}/export_{tag}_{ds} --scorer mahalanobis"
            jobs.append(
                (f"{tag}_{ds}_A_s{s}", RUN, model, f"{base} --epochs {ep} --seed {s}{extra}")
            )
    return jobs


def track_b(tag, model, seeds):
    jobs = []
    for ds, base, ep, k in (
        ("clinc", "--dataset clinc", 6, 30),
        ("banking77", "--dataset banking77", 8, 20),
        ("massive_en", "--dataset massive --locales en-US", 6, 15),
    ):
        for s in seeds:
            jobs.append(
                (f"{tag}_{ds}_B_h{k}_s{s}", RUN, model,
                 f"{base} --epochs {ep} --seed {s} --holdout {k} --unknown-head")
            )  # fmt: skip
    return jobs


JOBS = {
    "smoke": [("smoke_clinc_A", RUN, MINI, "--dataset clinc --epochs 1")],
    # Track A: main model (e5) over 3 seeds, MiniLM and XLM-R for the model-choice table.
    "final_a": (
        track_a("e5", E5, (42, 43, 44), export=("clinc",))
        + track_a("minilm", MINI, (42, 43, 44), export=("clinc",))
        + track_a("xlmr", XLMR, (42,))
    ),
    # Track B (open set) with the main model, 3 training seeds on the one pre-registered draw of
    # held-out classes, then the multilingual runs (E2f).
    "final_b": (
        track_b("e5", E5, (42, 43, 44))
        + [
            (
                "e5_massive_8loc_A",
                RUN,
                E5,
                f"--dataset massive --locales {LOC8} --epochs 3 --batch-size 64",
            ),
            (
                "e5_massive_8loc_enonly",
                RUN,
                E5,
                f"--dataset massive --locales {LOC8} --train-locales en-US --epochs 6",
            ),
            (
                "minilm_massive_8loc_A",
                RUN,
                MINI,
                f"--dataset massive --locales {LOC8} --epochs 3 --batch-size 64",
            ),
        ]
    ),  # fmt: skip
    # Hierarchy (E2d) and the ~500-row regime (E2e).
    "final_c": [
        ("e5_clinc_hier", HIER, E5, "--dataset clinc"),
        ("e5_massive_hier", HIER, E5, "--dataset massive"),
        ("e5_clinc_small500", SMALL, E5, "--dataset clinc"),
        ("e5_banking77_small500", SMALL, E5, "--dataset banking77"),
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
OUT.mkdir(exist_ok=True)
timings = {"gpu": gpu.stdout.strip(), "commit": sha, "mode": MODE, "jobs": {}}
for name, module, model, args in JOBS:
    t0 = time.time()
    r = sh(
        f"{sys.executable} -u -m engine.experiments.{module} --model {model} "
        f"--out {OUT}/{name}.json {args}",
        capture_output=True,
        text=True,
    )
    log = (r.stdout or "")[-20000:] + (r.stderr or "")[-8000:]
    (OUT / f"{name}.log").write_text(log, encoding="utf-8")
    timings["jobs"][name] = {"rc": r.returncode, "seconds": round(time.time() - t0, 1)}
    print(name, timings["jobs"][name], flush=True)
    (OUT / "timings.json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
timings["total_seconds"] = round(time.time() - t_all, 1)
(OUT / "timings.json").write_text(json.dumps(timings, indent=1), encoding="utf-8")
shutil.rmtree(repo, ignore_errors=True)  # keep /kaggle/working to the outputs only
shutil.rmtree(W / "cache", ignore_errors=True)
shutil.rmtree(W / "engine-data", ignore_errors=True)
print("done", timings["total_seconds"], flush=True)
