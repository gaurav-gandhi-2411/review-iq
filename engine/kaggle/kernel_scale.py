"""Kaggle kernel body: scale labelling, one stage (A, S or B) on two Ollama servers (one per T4). RESUMABLE.

Filled in by engine/kaggle/push_scale.py (__BRANCH__, __STAGE__, __ITEMS__, __BUDGET_H__). Reads the stage's item file
from a PRIVATE Kaggle dataset (licensed review text stays out of the repository) and, optionally, the cumulative
judge files of earlier sessions from a second private dataset (`.../judges/*.jsonl`). Work is cut into chunks of
`scale_plan.CHUNK` items; no new chunk starts once the time budget is spent, so a 9h or 12h session limit never
loses finished work. Output: /kaggle/working/out/judges/*.jsonl (ids and labels only, CUMULATIVE: prior lines are
copied in first) and out/status.json. No secret enters the kernel.
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
import threading
import time
import urllib.request

BRANCH = "__BRANCH__"
STAGE = "__STAGE__"
ITEMS_GLOB = "/kaggle/input/**/__ITEMS__"
PRIOR_GLOB = "/kaggle/input/**/judges/*.jsonl"
BUDGET_S = float("__BUDGET_H__") * 3600
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
# Amendments 13 and 15: one Ollama build, digest-checked (the digest GitHub publishes for the asset).
OLLAMA_URL = "https://github.com/ollama/ollama/releases/download/v0.40.6/ollama-linux-amd64.tar.zst"
OLLAMA_SHA256 = "c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0"
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
    tmp = "/tmp/ollama.tar.zst"
    urllib.request.urlretrieve(OLLAMA_URL, tmp)
    h = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    status["ollama_sha256"] = h.hexdigest()
    if (
        status["ollama_sha256"] != OLLAMA_SHA256
    ):  # one pinned build (spec Amendments 13 and 15), never "latest"
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
# Amendment 16: the pin must be RECORDED, not assumed. A status file without these fields is rejected by engine/labelling/panel_pin.py.
status["ollama_version"] = subprocess.run(
    [str(OLLAMA_DIR / "bin" / "ollama"), "--version"], capture_output=True, text=True, check=False
).stdout.strip()
status["model_digests"] = {}
for g in (0, 1):
    try:
        tags = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT[g]}/api/tags", timeout=10))
        status["model_digests"].update({m["name"]: m["digest"] for m in tags.get("models", [])})
    except Exception as e:  # noqa: BLE001 (recorded as missing; the pin check then rejects the run)
        status.setdefault("model_digest_errors", []).append(repr(e))
for p in servers:
    p.terminate()
shutil.rmtree(repo, ignore_errors=True)
write_status()
print(json.dumps(status, indent=1))
