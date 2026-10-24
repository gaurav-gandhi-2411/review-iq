"""Kaggle kernel body: the S31 B1 reply-judging panel (docs/research/reply-v22-eval-preregistration.md 1.3). Self-contained.

Reads `judge_items.jsonl` (item id + full rubric prompt, arm hidden) from a PRIVATE dataset, runs three non-gpt-oss judges on the
pinned Ollama build (digest-checked), temperature 0, JSON output, one retry on unparseable JSON. Writes out/verdicts/<judge>.jsonl
(item id + parsed verdict or null) and out/status.json with the Ollama sha256, version and model digests: a status without them is
rejected by the analysis.
"""

import glob
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import time
import urllib.request

OLLAMA_URL = "https://github.com/ollama/ollama/releases/download/v0.40.6/ollama-linux-amd64.tar.zst"
OLLAMA_SHA256 = "c164ab8b1b28e2a247c29719b8e404c423c5b83ae64c3ab4e59d50c7ff7fc5e0"
JUDGES = {"qwen3_8b": "qwen3:8b", "gemma2_9b": "gemma2:9b", "llama31_8b": "llama3.1:8b"}
W = pathlib.Path("/kaggle/working")
OUT = W / "out" / "verdicts"
OLLAMA_DIR = pathlib.Path("/opt/ollama")
HOST = "http://127.0.0.1:11434"
T0 = time.time()
status: dict = {"judges": {}}


def write_status() -> None:
    status["elapsed_s"] = round(time.time() - T0)
    (W / "out" / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")


def install_ollama() -> None:
    subprocess.run(f"{sys.executable} -m pip install -q zstandard", shell=True, check=True)
    import zstandard

    OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = "/tmp/ollama.tar.zst"
    urllib.request.urlretrieve(OLLAMA_URL, tmp)  # noqa: S310 (fixed https release URL, digest-checked below)
    h = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    status["ollama_sha256"] = h.hexdigest()
    if status["ollama_sha256"] != OLLAMA_SHA256:
        raise RuntimeError("ollama archive sha256 does not match the pinned digest")
    with (
        open(tmp, "rb") as fh,
        zstandard.ZstdDecompressor().stream_reader(fh) as rd,
        tarfile.open(fileobj=rd, mode="r|") as tf,
    ):
        tf.extractall(OLLAMA_DIR)  # noqa: S202 (a digest-verified release archive)


def start_server() -> subprocess.Popen:
    env = dict(
        os.environ,
        CUDA_VISIBLE_DEVICES="0",
        OLLAMA_HOST="127.0.0.1:11434",
        OLLAMA_MODELS="/tmp/ollama_models",
        OLLAMA_KEEP_ALIVE="60m",
        LD_LIBRARY_PATH=f"{OLLAMA_DIR}/lib/ollama:" + os.environ.get("LD_LIBRARY_PATH", ""),
    )
    log = open("/tmp/ollama.log", "w")  # noqa: SIM115 (the server keeps writing to it)
    p = subprocess.Popen(
        [str(OLLAMA_DIR / "bin" / "ollama"), "serve"], env=env, stdout=log, stderr=subprocess.STDOUT
    )
    for _ in range(90):
        try:
            urllib.request.urlopen(f"{HOST}/api/tags", timeout=2)  # noqa: S310 (localhost)
            return p
        except Exception:  # noqa: BLE001
            time.sleep(2)
    raise RuntimeError(
        "ollama did not start: " + pathlib.Path("/tmp/ollama.log").read_text()[-800:]
    )


def chat(model: str, prompt: str) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "format": "json",
            "options": {"temperature": 0, "seed": 42, "num_predict": 800, "num_ctx": 8192}}  # fmt: skip
    if model.startswith("qwen3"):
        body["think"] = False  # the rubric asks for a JSON object, not a reasoning trace
    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=600).read())["message"]["content"]  # noqa: S310 (localhost)


def parse(raw: str) -> dict | None:
    try:
        v = json.loads(raw)
    except json.JSONDecodeError:
        return None
    ok = (
        isinstance(v, dict)
        and isinstance(v.get("invented_details"), list)
        and isinstance(v.get("coherent"), bool)
    )
    return v if ok else None


items = [
    json.loads(x)
    for x in pathlib.Path(glob.glob("/kaggle/input/**/judge_items.jsonl", recursive=True)[0])
    .read_text(encoding="utf-8")
    .splitlines()
    if x.strip()
]
_panel = glob.glob("/kaggle/input/**/judges.json", recursive=True)
if _panel:  # S31 re-judge: the dataset may name its own panel ({"name": "ollama tag"}); otherwise the B1 default above
    JUDGES = json.loads(pathlib.Path(_panel[0]).read_text(encoding="utf-8"))
status["panel"] = JUDGES
OUT.mkdir(parents=True, exist_ok=True)
install_ollama()
server = start_server()
for name, tag in JUDGES.items():
    pull = subprocess.run([str(OLLAMA_DIR / "bin" / "ollama"), "pull", tag], env=dict(os.environ, OLLAMA_HOST="127.0.0.1:11434"),
                          capture_output=True, text=True)  # fmt: skip
    if pull.returncode != 0:
        raise RuntimeError(f"pull {tag} failed: {(pull.stderr or pull.stdout)[-300:]}")
    n_null = 0
    with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for it in items:
            v = None
            for _ in range(2):  # one retry on unparseable JSON (pre-registered)
                v = parse(chat(tag, it["prompt"]))
                if v is not None:
                    break
            n_null += v is None
            f.write(json.dumps({"item": it["item"], "verdict": v}, ensure_ascii=False) + "\n")
    status["judges"][name] = {"items": len(items), "null_verdicts": n_null}
    write_status()
status["ollama_version"] = subprocess.run(
    [str(OLLAMA_DIR / "bin" / "ollama"), "--version"], capture_output=True, text=True
).stdout.strip()
status["model_digests"] = {
    m["name"]: m["digest"]
    for m in json.load(urllib.request.urlopen(f"{HOST}/api/tags", timeout=10)).get("models", [])
}  # noqa: S310
write_status()
server.terminate()
print(json.dumps(status, indent=1))
