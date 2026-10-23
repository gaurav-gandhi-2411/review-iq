"""Kaggle kernel body (one T4): Amendment 16 run 4 - train the length arms, choose on validation, quantise the chosen arm, score on production numerics.

Filled in by engine/kaggle/push_run4.py (__BRANCH__, __ARMS__ = "H128:128:head,H256:256:head,...", __MODE__ = full|smoke, __EPOCHS__, __PATIENCE__).
Reads from ONE private Kaggle dataset: scale.jsonl, labels.json, tokenizer.json (+ config.json etc. in the same folder), and for MODE=full also train16.jsonl,
fresh16.jsonl and labels_train16.json (the four-judge consensus labels of the training additions). Review text never leaves the kernel; outputs are logits, ids,
small JSON and the chosen quantised model. The arms and the selection rule are registered in docs/specs/review-intent.md Amendment 16; the choice below is made by
code (engine/intent/run4_select.py) from validation numbers only, BEFORE the sealed set is scored with anything. No secret enters the kernel.
"""

import glob
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

BRANCH = "__BRANCH__"
ARMS = [a.split(":") for a in ["__ARMS__"]]  # [name, max_len, truncation]
MODE = "__MODE__"
EPOCHS = "__EPOCHS__"
PATIENCE = "__PATIENCE__"
REPO = "https://github.com/gaurav-gandhi-2411/review-iq.git"
W = pathlib.Path("/kaggle/working")
OUT = W / "out"
T0 = time.time()
status: dict = {"mode": MODE, "arms": ARMS, "steps": []}
os.environ.update(
    HF_HOME=str(W / "cache/hf"),
    TORCH_HOME=str(W / "cache/torch"),
    PYTHONIOENCODING="utf-8",
    TOKENIZERS_PARALLELISM="false",
)


def sh(cmd, **kw):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, **kw)


def step(name: str, **info) -> None:
    status["steps"].append({"step": name, "elapsed_s": round(time.time() - T0), **info})
    (OUT / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    print("STEP", name, info, flush=True)


def find(name, required=True):
    hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
    if not hits and required:
        raise SystemExit(f"{name} not found in the attached dataset")
    return hits[0] if hits else None


def run(cmd: str, log: pathlib.Path) -> None:
    r = sh(cmd, capture_output=True, text=True)
    log.write_text((r.stdout or "")[-30000:] + (r.stderr or "")[-8000:], encoding="utf-8")
    print((r.stdout or "")[-1500:], flush=True)
    if r.returncode:
        print((r.stderr or "")[-3000:], flush=True)
        raise SystemExit(f"command failed ({r.returncode}): {cmd[:120]}")


OUT.mkdir(parents=True, exist_ok=True)
sh(f"{sys.executable} -m pip install -q onnx onnxruntime==1.31.0 structlog", check=True)
repo = W / "review-iq"
if repo.exists():
    shutil.rmtree(repo)
sh(f"git clone --depth 1 --branch {BRANCH} {REPO} {repo}", check=True)
os.chdir(repo)
sys.path.insert(0, str(repo))
os.environ["PYTHONPATH"] = str(repo)
step(
    "cloned",
    branch=BRANCH,
    commit=sh("git rev-parse HEAD", capture_output=True, text=True).stdout.strip(),
)

SCALE, LABELS = find("scale.jsonl"), find("labels.json")
HF = str(pathlib.Path(find("tokenizer.json")).parent)
full = MODE == "full"
T16, F16, L16 = (
    (find("train16.jsonl"), find("fresh16.jsonl"), find("labels_train16.json"))
    if full
    else (None, None, None)
)
step("inputs", mode=MODE)

# 1. train every arm (fp32), the training additions only in full mode
for name, max_len, trunc in ARMS:
    d = OUT / name
    d.mkdir(exist_ok=True)
    extra = (
        f"--run4-labels {L16} --run4-manifest reports/labelling/train16/manifest.json --run4-texts {T16}"
        if full
        else ""
    )
    run(
        f"{sys.executable} -u -m engine.intent.train --labels {LABELS} --texts {SCALE} --manifest reports/labelling/scale/manifest.json "
        f"--out-dir {d} --device cuda --epochs {EPOCHS} --patience {PATIENCE} --save-weights --max-len {max_len} --truncation {trunc} {extra}",
        d / "train.log",
    )
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    step(
        "trained",
        arm=name,
        best_val_mean_macro_f1=meta["train_info"]["best_val_mean_macro_f1"],
        best_epoch=meta["train_info"]["best_epoch"],
        seconds=meta["seconds"],
    )

# 2. choose the arm on validation only (code, not judgement)
arms_arg = ",".join(f"{n}={OUT / n}:{L}:{t}" for n, L, t in ARMS)
l16_arg = f"--labels16 {L16}" if full else ""
if full:
    run(
        f"{sys.executable} -m engine.intent.run4_select --arms {arms_arg} --labels {LABELS} {l16_arg} --out {OUT / 'selection.json'}",
        OUT / "select.log",
    )
    sel = json.loads((OUT / "selection.json").read_text(encoding="utf-8"))
else:  # smoke: no targeted validation set exists; take the first arm so the plumbing below is exercised
    sel = {"chosen": ARMS[0][0], "ranking": [ARMS[0][0]], "reason": "smoke"}
    (OUT / "selection.json").write_text(json.dumps(sel), encoding="utf-8")
step("selected", chosen=sel["chosen"], ranking=sel["ranking"], eligible=sel.get("eligible"))
if not sel["chosen"]:
    raise SystemExit("no eligible arm: stop and report (Amendment 16)")

# 3. quantise the chosen arm (one fallback if it misses the 0.90 validation agreement), then score on the production numerics
info = {n: (int(L), t) for n, L, t in ARMS}
final = None
for cand in sel["ranking"][:2]:
    max_len, trunc = info[cand]
    arm_dir, qdir = OUT / cand, OUT / f"q_{cand}"
    t16 = (
        f"--train16-manifest reports/labelling/train16/manifest.json --train16-texts {T16}"
        if full
        else ""
    )
    run(
        f"{sys.executable} -m engine.intent.run4_prep --labels {LABELS} --scale-texts {SCALE} {t16} --arm-dir {arm_dir} --out-dir {qdir}",
        OUT / f"prep_{cand}.log",
    )
    calib = qdir / "calib.jsonl"
    run(
        f"{sys.executable} -u -m engine.intent.quantize_m1 quantize --weights {arm_dir / 'model.pt'} --hf {HF} --labels {LABELS} --calib-texts {calib} "
        f"--val-texts {qdir / 'val.jsonl'} --ref-pred-dir {arm_dir} --out {qdir} --max-len {max_len} --truncation {trunc}",
        OUT / f"quantize_{cand}.log",
    )
    qm = json.loads((qdir / "m1_manifest.json").read_text(encoding="utf-8"))
    step(
        "quantised",
        arm=cand,
        accepted=qm["accepted"],
        validation_agreement=qm["validation_agreement_with_fp32"],
        safe_layers=qm["safe_layers"],
    )
    if qm["accepted"]:
        final = cand
        break
if final is None:
    raise SystemExit(
        "neither the chosen arm nor its fallback passed the 0.90 agreement criterion: stop and report (Amendment 16)"
    )

max_len, trunc = info[final]
fin = OUT / f"final_{final}"
fin.mkdir(exist_ok=True)
run(
    f"{sys.executable} -u -m engine.intent.run4_finalize --model {OUT / ('q_' + final) / 'model_m1_auto.onnx'} --hf {HF} --labels {LABELS} --scale-texts {SCALE} "
    f"--arm-dir {OUT / final} --fresh16-texts {F16 or (OUT / ('q_' + final) / 'val.jsonl')} --max-len {max_len} --truncation {trunc} --variant run4_{final}_onnx_x64 --out {fin}",
    OUT / "finalize.log",
)
step("finalised", arm=final)

# 4. keep /kaggle/working to what is needed: the chosen quantised model and the small files; drop every fp32 checkpoint and intermediates
for n, _, _ in ARMS:
    (OUT / n / "model.pt").unlink(missing_ok=True)
for q in OUT.glob("q_*"):
    if q.name != f"q_{final}":
        shutil.rmtree(q, ignore_errors=True)
status["final_arm"] = final
step("done")
shutil.rmtree(repo, ignore_errors=True)
shutil.rmtree(W / "cache", ignore_errors=True)
