"""Train, export, evaluate and summarise ONE (arm, seed) run of the Amendment 8 comparison; reuses the existing CLIs.

    python -m engine.intent.seed_runs run --arm B --seed 43 --root D:/ml-cache/s23/seeds --out reports/intent/run3_seeds/runs

Arm A = run 2 recipe (`--safety-stratum exclude`), arm B = run 3 recipe (`include`); 30 epochs, patience 3, lr 5e-5, batch 32. Steps (each is
skipped when its output exists, so a killed run resumes): train on the local GPU (`engine.intent.train`), six CPU variants
(`engine.intent.quantize`), safety predictions for the GPU model and every variant (`engine.intent.safety_eval predict`), then the summary
JSON (`summarize`) holding exactly what `engine.intent.seeds` aggregates. Weights and prediction files stay under `--root` (not in git);
the summary carries their sha256 prefixes. No review text is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from engine import metrics as M
from engine.intent import safety_eval as SE
from engine.intent.evaluate import MIN_POSITIVES, _git_sha
from engine.intent.quantize import VARIANTS
from engine.intent.run3_compare import CORE, load_run, macro_arrays

LABELS = "reports/intent/labels/labels.json"
MANIFEST = "reports/labelling/scale/manifest.json"
LEDGER = "reports/labelling/ledger.json"
TEXTS = "D:/ml-cache/review-intent/scale.jsonl"
SAFETY_TEXTS = "D:/ml-cache/s23/safety_items.jsonl"
SAFETY_LABELS = "reports/labelling/safety/labels.jsonl"
MODES = ("threshold", "argmax")  # default decisioning, and the argmax ablation
OPS = ("D_serving_default", "S_val_f1_threshold", "P_argmax")


def _sh(*args: str, log: Path) -> None:
    with log.open("a", encoding="utf-8") as fh:
        subprocess.run([sys.executable, "-m", *args], check=True, stdout=fh, stderr=subprocess.STDOUT)  # noqa: S603


def dirs(arm: str, seed: int, root: Path) -> dict[str, Path]:
    base = root / f"{arm}{seed}"
    return {"base": base, "out": base / "out", "cpu": base / "cpu", "scratch": base / "scratch"}


def execute(arm: str, seed: int, root: Path) -> None:
    d = dirs(arm, seed, root)
    d["base"].mkdir(parents=True, exist_ok=True)
    log = d["base"] / "run.log"
    if not (d["out"] / "metadata.json").exists():
        train = ["engine.intent.train", "--labels", LABELS, "--texts", TEXTS, "--manifest", MANIFEST, "--out-dir", str(d["out"]),
                 "--epochs", "30", "--patience", "3", "--seed", str(seed), "--device", "cuda", "--save-weights",
                 "--safety-stratum", "include" if arm == "B" else "exclude"]  # fmt: skip
        if arm == "B":
            train += ["--safety-texts", SAFETY_TEXTS]
        _sh(*train, log=log)
    if not (d["cpu"] / "metadata.json").exists():
        _sh("engine.intent.quantize", "--weights", str(d["out"] / "model.pt"), "--pred-dir", str(d["out"]), "--labels", LABELS,
            "--texts", TEXTS, "--out-dir", str(d["cpu"]), "--scratch", str(d["scratch"]), log=log)  # fmt: skip
    common = ["--labels", LABELS, "--texts", SAFETY_TEXTS]
    targets = {"fp32_gpu": (d["out"], []), **{v: (d["cpu"] / v, ["--variant", v]) for v in VARIANTS}}
    for where, extra in targets.values():
        out = where / "safety_pred.json"
        if out.exists():
            continue
        meta = str(d["cpu"] / "metadata.json") if extra else str(d["out"] / "metadata.json")
        _sh("engine.intent.safety_eval", "predict", "--weights", str(d["out"] / "model.pt"), "--meta", meta,
            "--train-meta", str(d["out"] / "metadata.json"), *common, *extra, "--out", str(out), log=log)  # fmt: skip


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def base_macros(labels: dict, preds: dict, mode: str) -> dict[str, float | None]:
    """Evaluation A: macro-F1 over classes with >= 20 sealed positives, per core task (same rule as run3_compare)."""
    out: dict[str, float | None] = {}
    for t in CORE:
        _, y, pred = macro_arrays(labels, preds[t], t, mode)
        scored = [c for c in range(len(labels["classes"][t])) if int((y == c).sum()) >= MIN_POSITIVES]
        out[t] = round(float(M.macro_f1(y, pred, scored)), 4) if len(scored) >= 2 else None
    return out


def summarize(arm: str, seed: int, root: Path) -> dict:
    d = dirs(arm, seed, root)
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    labels, manifest = load(LABELS), load(MANIFEST)
    excluded = set(load(LEDGER).get("sealed_excluded", []))
    ctx = SE.load_context(SAFETY_LABELS, SAFETY_TEXTS, LEDGER)
    tm = load(d["out"] / "metadata.json")
    where = {"fp32_gpu": d["out"]} | {v: d["cpu"] / v for v in VARIANTS}
    run: dict = {
        "arm": arm, "seed": seed, "summary_commit": _git_sha(), "train_commit": tm["commit"], "device": tm["device"],
        "torch": tm["torch"], "config": tm["config"], "safety_stratum": tm["safety_stratum"],
        "n_train_items": tm["n_items"]["train"], "best_epoch": tm["train_info"]["best_epoch"],
        "stopped_early": tm["train_info"]["stopped_early"], "best_val_mean_macro_f1": tm["train_info"]["best_val_mean_macro_f1"],
        "train_seconds": tm["seconds"], "variants": {},
    }  # fmt: skip
    for name, p in where.items():
        preds = load_run(p, manifest, excluded)
        sp = SE.load_pred(p / "safety_pred.json")
        res = SE.evaluate({name: sp}, name, ctx["urgency"], ctx["shas"], ctx["groups"], ctx["excluded"], 1000)["runs"][name]
        run["variants"][name] = {
            "A": {m: base_macros(labels, preds, m) for m in MODES},
            "B": {op: {k: res[op][k] for k in ("recall", "precision", "independent_positive_groups")} for op in OPS if op in res},
            "thresholds": {"D_base_val": res.get("D_threshold_on_base_val"), "S_safety_val": res["S_threshold_on_safety_val"]},
            "decision": load(p / "pred_urgency.json")["decision"]["thresholds"],
            "pred_sha256_16": {f.name: _sha(f) for f in sorted(p.glob("pred_*.json")) + [p / "safety_pred.json"]},
        }  # fmt: skip
    return run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summarize"])
    ap.add_argument("--arm", required=True, choices=["A", "B"])
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--root", required=True, help="where weights and prediction files live (not in git)")
    ap.add_argument("--out", required=True, help="directory for <arm>_seed<N>.json")
    a = ap.parse_args()
    if a.cmd == "run":
        execute(a.arm, a.seed, Path(a.root))
    res = summarize(a.arm, a.seed, Path(a.root))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"{a.arm}_seed{a.seed}.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.arm}_seed{a.seed}.json")


if __name__ == "__main__":
    main()
