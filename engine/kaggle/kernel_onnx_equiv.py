"""Kaggle kernel body (CPU): score a fixed list of reviews with the static-int8 ONNX model on THIS machine's CPU, with and without the
production-numerics pin (S30; app/core/classify/ort_numerics.py), one review per forward, and record the CPU's relevant instruction flags.

Filled in by engine/kaggle/push_onnx_equiv.py (__BRANCH__). Reads `model.onnx`, `tokenizer.json` and `items.jsonl` ({id, text}) from a
PRIVATE Kaggle dataset; review text never leaves the kernel. Output: /kaggle/working/out/{logits.npz, run_result.json}: raw logits per
item for `pinned` and `unpinned`, the ids, and the CPU facts. No secret enters the kernel.
"""

import glob
import importlib.util
import json
import pathlib
import platform
import subprocess
import sys
import time

BRANCH = "__BRANCH__"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out"
OUT.mkdir(parents=True, exist_ok=True)


def sh(cmd: str, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


sh(f"{sys.executable} -m pip install -q onnxruntime==1.31.0 tokenizers==0.22.2 numpy", check=True)
sh(f"git clone --depth 1 --branch {BRANCH} {REPO} /tmp/repo", check=True)

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "ort_numerics", "/tmp/repo/app/core/classify/ort_numerics.py"
)
ort_numerics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ort_numerics)

d = pathlib.Path(glob.glob("/kaggle/input/**/model.onnx", recursive=True)[0]).parent
items = [
    json.loads(x) for x in (d / "items.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
]
tok = Tokenizer.from_file(str(d / "tokenizer.json"))
tok.enable_truncation(max_length=128)
tok.enable_padding(pad_id=1, pad_token="<pad>")

cpuinfo = pathlib.Path("/proc/cpuinfo").read_text()
flags = next(
    (ln.split(":", 1)[1].split() for ln in cpuinfo.splitlines() if ln.startswith("flags")), []
)
model_name = next(
    (ln.split(":", 1)[1].strip() for ln in cpuinfo.splitlines() if ln.startswith("model name")), "?"
)
facts = {
    "cpu_model": model_name,
    "n_logical_cpus": cpuinfo.count("processor\t:"),
    "flags_of_interest": {
        f: (f in flags)
        for f in ("avx2", "avx512f", "avx512_vnni", "avx_vnni", "amx_int8", "amx_tile")
    },
    "onnxruntime": ort.__version__,
    "python": platform.python_version(),
    "model_sha256": subprocess.run(
        ["sha256sum", str(d / "model.onnx")], capture_output=True, text=True
    ).stdout.split()[0],
    "n_items": len(items),
}
print(json.dumps(facts), flush=True)


def run(pin: bool) -> np.ndarray:
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    so.inter_op_num_threads = 1
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if pin:
        ort_numerics.apply_numerics_pin(so)
    sess = ort.InferenceSession(str(d / "model.onnx"), so, providers=["CPUExecutionProvider"])
    rows = []
    for it in items:
        enc = tok.encode(it["text"])
        feed = {
            "input_ids": np.array([enc.ids], dtype=np.int64),
            "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
        }
        rows.append(sess.run(["logits"], feed)[0].astype(np.float32)[0])
    return np.stack(rows)


t0 = time.time()
pinned = run(True)
unpinned = run(False)
facts["seconds"] = round(time.time() - t0, 1)
facts["max_abs_logit_diff_pinned_vs_unpinned_on_this_cpu"] = float(np.abs(pinned - unpinned).max())
np.savez(
    OUT / "logits.npz", pinned=pinned, unpinned=unpinned, ids=np.array([i["id"] for i in items])
)
(OUT / "run_result.json").write_text(json.dumps(facts, indent=1), encoding="utf-8")
print("done", json.dumps(facts), flush=True)
