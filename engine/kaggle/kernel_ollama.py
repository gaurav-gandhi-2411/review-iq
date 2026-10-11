"""Kaggle kernel body: run the review-intent judge panel on two Ollama servers (one per T4).

Filled in by engine/kaggle/push_ollama.py. Reads the pilot sample from a PRIVATE Kaggle dataset
(licensed review text stays out of the repository), clones the public repo for the judge code, installs
Ollama, pulls the models, and writes judge files (ids and labels only, no review text) to /kaggle/working/out.
No secret enters the kernel.
"""

import glob
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

BRANCH = "__BRANCH__"
ITEMS_GLOB = "/kaggle/input/**/__ITEMS__"
PROMPT_VERSION = "__PV__"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out" / "judges"
OLLAMA_DIR = pathlib.Path("/opt/ollama")
# (judge file name, ollama model, family)  -- one server (GPU) runs two judges in sequence
PLAN = {
    0: [("llama31_8b", "llama3.1:8b", "Meta"), ("qwen3_8b", "qwen3:8b", "Alibaba")],
    1: [("gemma2_9b", "gemma2:9b", "Google"), ("mistral_7b", "mistral:7b", "Mistral")],
}
PORT = {0: 11434, 1: 11435}
status: dict = {"models": {}}


def sh(cmd, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


def install_ollama() -> None:
    sh(f"{sys.executable} -m pip install -q zstandard", check=True)
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
        OLLAMA_MODELS=f"/tmp/ollama_models_{gpu}",
        OLLAMA_KEEP_ALIVE="60m",
        LD_LIBRARY_PATH=f"{OLLAMA_DIR}/lib/ollama:" + os.environ.get("LD_LIBRARY_PATH", ""),
    )
    log = open(f"/tmp/ollama{gpu}.log", "w")  # noqa: SIM115 (must outlive this function: the server keeps writing)
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
        f"ollama {gpu} did not start; log: "
        + pathlib.Path(f"/tmp/ollama{gpu}.log").read_text()[-800:]
    )


def worker(gpu: int, items: str) -> None:
    env = dict(
        os.environ, OLLAMA_HOST=f"127.0.0.1:{PORT[gpu]}", OLLAMA_MODELS=f"/tmp/ollama_models_{gpu}"
    )
    for name, model, family in PLAN[gpu]:
        t0 = time.time()
        try:
            pull = subprocess.run([str(OLLAMA_DIR / "bin" / "ollama"), "pull", model], env=env,
                                  capture_output=True, text=True)  # fmt: skip
            if pull.returncode != 0:
                raise RuntimeError("pull failed: " + (pull.stderr or pull.stdout)[-300:])
            r = subprocess.run(
                [sys.executable, "-m", "engine.labelling.judge", "--items", items, "--model", model,
                 "--out", str(OUT / f"{name}.jsonl"), "--base-url", f"http://127.0.0.1:{PORT[gpu]}",
                "--prompt-version",
                PROMPT_VERSION],
                capture_output=True, text=True,
            )  # fmt: skip
            status["models"][name] = {"family": family, "model": model, "rc": r.returncode,
                                      "seconds": round(time.time() - t0), "tail": (r.stdout or r.stderr)[-300:]}  # fmt: skip
        except Exception as e:  # noqa: BLE001
            status["models"][name] = {"family": family, "model": model, "error": str(e)[:400]}
        (W / "out").mkdir(parents=True, exist_ok=True)
        (W / "out" / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")


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
items = glob.glob(ITEMS_GLOB, recursive=True)[0]
OUT.mkdir(parents=True, exist_ok=True)
install_ollama()
servers = [start_server(g) for g in (0, 1)]
threads = [threading.Thread(target=worker, args=(g, items)) for g in (0, 1)]
for t in threads:
    t.start()
for t in threads:
    t.join()
for p in servers:
    p.terminate()
shutil.rmtree(repo, ignore_errors=True)
print(json.dumps(status, indent=1))
