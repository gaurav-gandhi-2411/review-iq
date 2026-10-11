"""Kaggle kernel body: scale labelling, one stage (A, S or B) on two Ollama servers (one per T4). RESUMABLE.

Filled in by engine/kaggle/push_scale.py (__BRANCH__, __STAGE__, __ITEMS__, __BUDGET_H__). Reads the stage's item file
from a PRIVATE Kaggle dataset (licensed review text stays out of the repository) and, optionally, the cumulative
judge files of earlier sessions from a second private dataset (`.../judges/*.jsonl`). Work is cut into chunks of
`scale_plan.CHUNK` items; no new chunk starts once the time budget is spent, so a 9h or 12h session limit never
loses finished work. Output: /kaggle/working/out/judges/*.jsonl (ids and labels only, CUMULATIVE: prior lines are
copied in first) and out/status.json. No secret enters the kernel.
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
STAGE = "__STAGE__"
ITEMS_GLOB = "/kaggle/input/**/__ITEMS__"
PRIOR_GLOB = "/kaggle/input/**/judges/*.jsonl"
BUDGET_S = float("__BUDGET_H__") * 3600
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out" / "judges"
OLLAMA_DIR = pathlib.Path("/opt/ollama")
PORT = {0: 11434, 1: 11435}
T0 = time.time()
status: dict = {"stage": STAGE, "judges": {}, "budget_hit": False}


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
        OLLAMA_MAX_LOADED_MODELS="2",  # stage A alternates two ~5 GB models per chunk; fits one 15 GB T4
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
        f"ollama {gpu} did not start; log: "
        + pathlib.Path(f"/tmp/ollama{gpu}.log").read_text()[-800:]
    )


def write_status() -> None:
    status["elapsed_s"] = round(time.time() - T0)
    (W / "out").mkdir(parents=True, exist_ok=True)
    (W / "out" / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")


def worker(gpu: int, all_items: list) -> None:
    from engine.labelling import scale_plan as SP

    judges, sharded = SP.STAGES[STAGE]
    items = [r for i, r in enumerate(all_items) if SP.shard_of(i) == gpu] if sharded else all_items
    env = dict(
        os.environ, OLLAMA_HOST=f"127.0.0.1:{PORT[gpu]}", OLLAMA_MODELS=f"/tmp/ollama_models_{gpu}"
    )
    names = judges[gpu]
    try:
        for name in names:
            pull = subprocess.run([str(OLLAMA_DIR / "bin" / "ollama"), "pull", SP.JUDGES[name]], env=env,
                                  capture_output=True, text=True)  # fmt: skip
            if pull.returncode != 0:
                raise RuntimeError("pull failed: " + (pull.stderr or pull.stdout)[-300:])
        progress = True
        while progress:
            progress = False
            for name in names:
                out = OUT / (f"{name}__shard{gpu}.jsonl" if sharded else f"{name}.jsonl")
                todo = SP.pending_chunks(items, SP.done_ids(out))
                if not todo:
                    continue
                if time.time() - T0 > BUDGET_S:
                    status["budget_hit"] = True
                    return
                chunk = pathlib.Path(f"/tmp/chunk_{gpu}_{name}.jsonl")
                chunk.write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in todo[0]),
                    encoding="utf-8",
                )
                r = subprocess.run(
                    [sys.executable, "-m", "engine.labelling.judge", "--items", str(chunk), "--model", SP.JUDGES[name],
                     "--out", str(out), "--base-url", f"http://127.0.0.1:{PORT[gpu]}", "--prompt-version", "v3"],
                    capture_output=True, text=True,
                )  # fmt: skip
                if r.returncode != 0:
                    raise RuntimeError(
                        f"judge failed rc={r.returncode}: " + (r.stderr or r.stdout)[-300:]
                    )
                progress = True
                status["judges"][f"{name}__gpu{gpu}"] = {
                    "done": len(SP.done_ids(out)),
                    "of": len(items),
                }
                write_status()
    except Exception as e:  # noqa: BLE001
        status["judges"][f"gpu{gpu}_error"] = str(e)[:400]
    finally:
        write_status()


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
OUT.mkdir(parents=True, exist_ok=True)
for prior in glob.glob(
    PRIOR_GLOB, recursive=True
):  # cumulative: earlier sessions' lines are kept and skipped
    dst = OUT / pathlib.Path(prior).name
    if not dst.exists():
        shutil.copy(prior, dst)
items_file = glob.glob(ITEMS_GLOB, recursive=True)[0]
all_items = [
    json.loads(x)
    for x in pathlib.Path(items_file).read_text(encoding="utf-8").splitlines()
    if x.strip()
]
install_ollama()
servers = [start_server(g) for g in (0, 1)]
threads = [threading.Thread(target=worker, args=(g, all_items)) for g in (0, 1)]
for t in threads:
    t.start()
for t in threads:
    t.join()
for p in servers:
    p.terminate()
shutil.rmtree(repo, ignore_errors=True)
write_status()
print(json.dumps(status, indent=1))
