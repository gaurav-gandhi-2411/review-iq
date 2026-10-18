"""Kaggle kernel body: the gpt-oss-20b-local moat arm (spec Amendment 9) on two Ollama servers (one per T4). RESUMABLE.

Filled in by engine/kaggle/push_moat20b.py (__BRANCH__, __MODE__ = smoke|full, __BUDGET_H__, __ARM__ = zeroshot|fewshot;
the few-shot arm of spec Amendment 10 also reads a private `shots.jsonl` {id, text, gold}). Reads `items.jsonl`
({id, text} for the sealed items only, in sealed order) from a PRIVATE Kaggle dataset; review text stays out of the repo and out
of every output. Output: /kaggle/working/out/{shards/*.jsonl, status.json, run_result.json, *interim300.json}; rows carry ids,
parsed answers and token counts only. Prior shards (a `shards/` folder in a second private dataset) are copied in first so a
later session resumes. No secret enters the kernel.
"""

import glob
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

BRANCH = "__BRANCH__"
MODE = "__MODE__"
ARM = "__ARM__"
NO_JSON_MODE = "__NOJSON__" == "1"  # Amendment 9 fallback: rerun without response_format
BUDGET_S = float("__BUDGET_H__") * 3600
MAX_TOKENS = "__MAXTOK__"  # spec Amendment 12; 600 and "none" reproduce Amendments 9 and 10
EFFORT = "__EFFORT__"  # none | low | medium | high
# Amendment 12 canary ladder, fixed before any rung runs: (reasoning effort, max_tokens); stop at the first rung with 0 empties.
RUNGS = [("low", 1500), ("low", 2500), ("medium", 2900)]
MODEL = "gpt-oss:20b"
# Amendment 13: one Ollama build for every arm. 0.40.6 is what both v2 arms ran on; the digest is the one GitHub publishes for the asset.
OLLAMA_URL = "https://github.com/ollama/ollama/releases/download/v0.40.6/ollama-linux-amd64.tar.zst"
OLLAMA_SHA256 = "c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out"
OLLAMA_DIR = pathlib.Path("/opt/ollama")
MODELS = "/tmp/ollama_models"  # one shared, read-only-in-use model store for both servers
PORT = {0: 11434, 1: 11435}
T0 = time.time()
status: dict = {"mode": MODE, "arm": ARM, "model_tag": MODEL, "no_json_mode": NO_JSON_MODE}


def sh(cmd, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


def install_ollama() -> None:
    sh(f"{sys.executable} -m pip install -q zstandard httpx", check=True)
    import zstandard

    OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = "/tmp/ollama.tar.zst"
    urllib.request.urlretrieve(OLLAMA_URL, tmp)
    h = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    status["ollama_sha256"] = h.hexdigest()
    if (
        status["ollama_sha256"] != OLLAMA_SHA256
    ):  # one pinned build (spec Amendment 13), never "latest"
        raise RuntimeError("ollama archive sha256 does not match the pinned digest")
    with (
        open(tmp, "rb") as fh,
        zstandard.ZstdDecompressor().stream_reader(fh) as rd,
        tarfile.open(fileobj=rd, mode="r|") as tf,
    ):
        tf.extractall(OLLAMA_DIR)  # noqa: S202 (a digest-verified release archive)
    if not (OLLAMA_DIR / "bin" / "ollama").exists():
        raise RuntimeError("ollama binary missing after extraction")


def start_server(gpu: int) -> subprocess.Popen:
    env = dict(
        os.environ,
        CUDA_VISIBLE_DEVICES=str(gpu),
        OLLAMA_HOST=f"127.0.0.1:{PORT[gpu]}",
        OLLAMA_MODELS=MODELS,
        OLLAMA_KEEP_ALIVE="24h",
        OLLAMA_CONTEXT_LENGTH="4096",
        OLLAMA_NUM_PARALLEL="1",  # sequential requests per server: temperature-0 decoding is not batched with neighbours
        LD_LIBRARY_PATH=f"{OLLAMA_DIR}/lib/ollama:" + os.environ.get("LD_LIBRARY_PATH", ""),
    )
    log = open(f"/tmp/ollama{gpu}.log", "w")  # noqa: SIM115 (the server keeps writing to it)
    p = subprocess.Popen(
        [str(OLLAMA_DIR / "bin" / "ollama"), "serve"], env=env, stdout=log, stderr=subprocess.STDOUT
    )
    for _ in range(90):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT[gpu]}/api/tags", timeout=2)
            return p
        except Exception:  # noqa: BLE001
            time.sleep(2)
    raise RuntimeError(
        "ollama did not start: " + pathlib.Path(f"/tmp/ollama{gpu}.log").read_text()[-800:]
    )


def post(gpu: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT[gpu]}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=900).read())


def write_status() -> None:
    status["elapsed_s"] = round(time.time() - T0)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")


repo = W / "review-iq"
if repo.exists():
    shutil.rmtree(repo)
sh(f"git clone --depth 1 --branch {BRANCH} {REPO} {repo}", check=True)
os.chdir(repo)
sys.path.insert(0, str(repo))
status["commit"] = subprocess.run(
    "git rev-parse HEAD", shell=True, capture_output=True, text=True
).stdout.strip()
status["gpu"] = sh(
    "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", capture_output=True, text=True
).stdout.strip()
(OUT / "shards").mkdir(parents=True, exist_ok=True)
for prior in glob.glob("/kaggle/input/**/shards/*.jsonl", recursive=True):
    dst = OUT / "shards" / pathlib.Path(prior).name
    if not dst.exists():
        shutil.copy(prior, dst)
items_file = glob.glob("/kaggle/input/**/items.jsonl", recursive=True)[0]
with open(items_file, encoding="utf-8") as fh:
    status["n_items"] = sum(1 for x in fh if x.strip())
shots_file = None
if ARM in ("fewshot", "both"):
    shots_file = glob.glob("/kaggle/input/**/shots.jsonl", recursive=True)[0]
    with open(shots_file, encoding="utf-8") as fh:
        shot_rows = [json.loads(x) for x in fh if x.strip()]
    status["shot_ids"] = [r["id"] for r in shot_rows]  # ids only, never text
install_ollama()
servers = [start_server(g) for g in (0, 1)]
status["ollama_version"] = sh(  # with the server up, --version prints the server version first
    f"OLLAMA_HOST=127.0.0.1:{PORT[0]} {OLLAMA_DIR}/bin/ollama --version",
    capture_output=True,
    text=True,
).stdout.strip()
try:
    pull = subprocess.run(
        [str(OLLAMA_DIR / "bin" / "ollama"), "pull", MODEL],
        env=dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{PORT[0]}", OLLAMA_MODELS=MODELS),
        capture_output=True,
        text=True,
    )
    status["pull_rc"] = pull.returncode
    if pull.returncode != 0:
        raise RuntimeError("pull failed: " + (pull.stderr or pull.stdout)[-300:])
    tags = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT[0]}/api/tags").read())
    status["tags"] = [m for m in tags.get("models", []) if m.get("name") == MODEL]
    show = post(0, "/api/show", {"model": MODEL})
    status["show"] = {k: show.get(k) for k in ("details", "capabilities", "modified_at")}
    status["show_parameters"] = show.get("parameters")
    args = [
        "--items",
        items_file,
        "--out",
        str(OUT),
        "--urls",
        ",".join(f"http://127.0.0.1:{PORT[g]}" for g in (0, 1)),
    ]
    args += ["--model", MODEL, "--budget-h", str(BUDGET_S / 3600)]
    if shots_file:
        args += ["--shots", shots_file]
    if NO_JSON_MODE:
        args += ["--no-json-mode"]
    if MODE == "smoke":
        args += ["--limit", "20"]
    gen = ["--max-tokens", MAX_TOKENS] + (
        [] if EFFORT == "none" else ["--reasoning-effort", EFFORT]
    )
    runner = [sys.executable, "-m", "engine.intent.llm_moat_local", "run"]
    if (
        MODE == "canary"
    ):  # few-shot prompt, first 30 sealed items per rung; rows are discarded, only counts are kept
        status["canary"] = []
        for i, (eff, mt) in enumerate(RUNGS):
            cdir = OUT / f"canary_L{i + 1}"
            cargs = (
                args[:2] + ["--out", str(cdir)] + args[4:] + ["--limit", "30", "--stem", "canary"]
            )
            cargs += ["--max-tokens", str(mt), "--reasoning-effort", eff]
            cr = subprocess.run([*runner, *cargs], capture_output=True, text=True)
            rows = [
                json.loads(x)
                for f in sorted((cdir / "shards").glob("*.jsonl"))
                for x in f.read_text().splitlines()
                if x.strip()
            ]
            rung = {
                "rung": i + 1,
                "effort": eff,
                "max_tokens": mt,
                "rc": cr.returncode,
                "n": len(rows),
            }
            rung["empty"] = sum(r["ans"] is None for r in rows)
            rung["mean_total_tokens"] = round(sum(r["tokens"] for r in rows) / max(1, len(rows)), 1)
            rung["max_total_tokens"] = max((r["tokens"] for r in rows), default=0)
            rung["finish_length"] = sum(r.get("fin") == "length" for r in rows)
            status["canary"].append(rung)
            if cr.returncode == 0 and rung["n"] == 30 and rung["empty"] == 0:
                break
        r = subprocess.CompletedProcess([], 0, "", "")
    elif (
        MODE == "effort"
    ):  # Amendment 13: is reasoning_effort honoured? zero-shot, first 20 items, rows discarded
        status["effort"] = []
        for eff in ("none", "low", "medium", "high"):
            edir = OUT / f"effort_{eff}"
            eargs = args[:2] + ["--out", str(edir)] + args[4:]
            eargs += ["--limit", "20", "--stem", "effort", "--max-tokens", "2900"]
            if eff != "none":
                eargs += ["--reasoning-effort", eff]
            er = subprocess.run([*runner, *eargs], capture_output=True, text=True)
            rows = [
                json.loads(x)
                for f in sorted((edir / "shards").glob("*.jsonl"))
                for x in f.read_text().splitlines()
                if x.strip()
            ]
            status["effort"].append(
                {
                    "effort": eff,
                    "rc": er.returncode,
                    "n": len(rows),
                    "empty": sum(x["ans"] is None for x in rows),
                    "finish_length": sum(x.get("fin") == "length" for x in rows),
                    "completion_tokens": {x["id"]: x["tokens"] - x.get("ptoks", 0) for x in rows},
                }
            )
        r = subprocess.CompletedProcess([], 0, "", "")
    elif (
        ARM == "both"
    ):  # Amendment 12 full run: few-shot to the stopping rule, then zero-shot on the first 300 with identical settings
        r = subprocess.run(
            [*runner, *args, *gen, "--stem", "openai_gpt-oss-20b__local_fewshot_v2"],
            capture_output=True, text=True,
        )  # fmt: skip
        status["fewshot_rc"] = r.returncode
        status["fewshot_stdout_tail"] = r.stdout[-600:]
        zargs = [
            a for i, a in enumerate(args) if a != "--shots" and (i == 0 or args[i - 1] != "--shots")
        ]
        r = subprocess.run(
            [*runner, *zargs, *gen, "--limit", "300", "--stem", "openai_gpt-oss-20b__local_v2"],
            capture_output=True, text=True,
        )  # fmt: skip
    else:
        r = subprocess.run([*runner, *args, *gen], capture_output=True, text=True)
    status["run_rc"] = r.returncode
    if (
        shots_file and MODE == "smoke"
    ):  # context budget: prompt tokens with the prefix cache defeated
        pr = subprocess.run(
            [sys.executable, "-m", "engine.intent.llm_moat_local", "probe", *args[:4], "--urls",
             f"http://127.0.0.1:{PORT[0]}", "--model", MODEL, "--shots", shots_file],
            capture_output=True, text=True,
        )  # fmt: skip
        status["probe_rc"] = pr.returncode
        status["probe_stdout_tail"] = pr.stdout[-400:]
        status["probe_stderr_tail"] = pr.stderr[-400:]
    status["run_stdout_tail"] = r.stdout[-1500:]
    status["run_stderr_tail"] = r.stderr[-800:]
except Exception as e:  # noqa: BLE001
    status["error"] = str(e)[:500]
finally:
    status["nvidia_smi_after"] = sh(
        "nvidia-smi --query-gpu=name,memory.used --format=csv,noheader",
        capture_output=True,
        text=True,
    ).stdout.strip()
    status["offload_log"] = [
        ln[-220:]
        for g in (0, 1)
        for ln in pathlib.Path(f"/tmp/ollama{g}.log").read_text(errors="replace").splitlines()
        if "offload" in ln or "layers" in ln
    ][:12]
    for p in servers:
        p.terminate()
    shutil.rmtree(repo, ignore_errors=True)
    write_status()
print(json.dumps(status, indent=1))
