"""Kaggle kernel body: the gpt-oss-20b-local moat arm (spec Amendment 7) on two Ollama servers (one per T4). RESUMABLE.

Filled in by engine/kaggle/push_moat20b.py (__BRANCH__, __MODE__ = smoke|full, __BUDGET_H__). Reads `items.jsonl`
({id, text} for the sealed items only, in sealed order) from a PRIVATE Kaggle dataset; review text stays out of the repo and out
of every output. Output: /kaggle/working/out/{shards/*.jsonl, status.json, run_result.json, *interim300.json}; rows carry ids,
parsed answers and token counts only. Prior shards (a `shards/` folder in a second private dataset) are copied in first so a
later session resumes. No secret enters the kernel.
"""

import glob
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
BUDGET_S = float("__BUDGET_H__") * 3600
MODEL = "gpt-oss:20b"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out"
OLLAMA_DIR = pathlib.Path("/opt/ollama")
MODELS = "/tmp/ollama_models"  # one shared, read-only-in-use model store for both servers
PORT = {0: 11434, 1: 11435}
T0 = time.time()
status: dict = {"mode": MODE, "model_tag": MODEL}


def sh(cmd, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


def install_ollama() -> None:
    sh(f"{sys.executable} -m pip install -q zstandard httpx", check=True)
    import zstandard

    OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
    last = None
    for url, kind in (
        ("https://ollama.com/download/ollama-linux-amd64.tar.zst", "zst"),
        ("https://ollama.com/download/ollama-linux-amd64.tgz", "tgz"),
    ):
        try:
            tmp = f"/tmp/ollama.{kind}"
            urllib.request.urlretrieve(url, tmp)
            if kind == "zst":
                with (
                    open(tmp, "rb") as fh,
                    zstandard.ZstdDecompressor().stream_reader(fh) as rd,
                    tarfile.open(fileobj=rd, mode="r|") as tf,
                ):
                    tf.extractall(OLLAMA_DIR)
            else:
                with tarfile.open(tmp) as tf:
                    tf.extractall(OLLAMA_DIR)
            if (OLLAMA_DIR / "bin" / "ollama").exists():
                return
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"could not install ollama: {last}")


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
install_ollama()
status["ollama_version"] = sh(
    f"{OLLAMA_DIR}/bin/ollama --version", capture_output=True, text=True
).stdout.strip()
servers = [start_server(g) for g in (0, 1)]
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
    if MODE == "smoke":
        args += ["--limit", "20"]
    r = subprocess.run(
        [sys.executable, "-m", "engine.intent.llm_moat_local", "run", *args],
        capture_output=True,
        text=True,
    )
    status["run_rc"] = r.returncode
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
