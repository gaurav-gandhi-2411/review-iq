"""Plain-prompted LLM with verbalised confidence vs the fine-tuned encoder, on the sealed review set (S23 item 1).

PRE-REGISTERED (committed before any LLM call is made on the sealed set):
  * Items: sealed test items (split == test) minus `sealed_excluded` in the leakage ledger; gold = four-judge
    consensus labels (SILVER) from reports/intent/labels/labels.json.
  * Tasks: needs_action, broad_intent, sentiment, urgency (aspect sentiment is not compared: stated limit).
  * LLM arm: one zero-shot call per item returning all four tasks as JSON, each with an integer confidence 0-100.
    Same class definitions as the labelling spec in one short paragraph, NO few-shots, temperature 0, fixed seed.
    Models: openai/gpt-oss-20b and openai/gpt-oss-120b on the dedicated Groq org via the benchmark key
    (`benchmark_groq_key.load_benchmark_groq_key`), paced under the TPM limit and a per-model daily token budget
    (default 180,000 of the 200,000 TPD). The run is resumable across days.
  * Metric: for each task, coverage at which precision (vs consensus) reaches 0.95, using the SAME function as the
    BANKING77 comparison (`engine.experiments.selective.summarize`), item bootstrap 1,000 resamples, 95 percent CI.
    This is the ORACLE curve (best threshold on the sealed set), which FAVOURS both arms equally; the deployable
    comparison for the encoder is the conservative rule already in eval_run1.json. Ties in verbalised confidence
    produce coarse steps; reported as is. Unparseable or missing answers count as wrong at confidence 0 (kept, not
    dropped). CIs resample items, not products (no product ids in the scale corpus): stated beside every interval.
  * No claim of superiority unless the CIs of the 0.95-precision coverage do not overlap.
  * SEQUENTIAL STOPPING RULE (added before any further calls; the first ~100 rows per model were collected
    under the plain protocol above): after n = 300 sealed items per model and arm, for each task compare the
    95 percent CI of coverage at precision 0.95 of the LLM and of the encoder on the SAME first 300 items.
    Non-overlap: that task is decided and gets no more items; overlap: continue to all sealed items. The run
    for a model/arm stops once all four tasks are decided. The report states which rule fired per task.
  * FEW-SHOT ARM (gpt-oss-120b): the same prompt plus up to 6 worked examples drawn from TRAINING items only
    (deterministic: shuffled with Random(42), greedy cover of unseen task/class pairs, review <= 220 chars).
    Example ids are written to `fewshot_ids.json`; sealed and validation items are never eligible. Example
    confidences are fixed at 90 (illustrative); this may bias calibration and is disclosed.
  * Quota: the arm may use up to 180,000 tokens per model per UTC day (U4d is paused by decision until the
    comparison finishes); prod keeps the remaining headroom.

    python -m engine.intent.llm_moat run --model openai/gpt-oss-20b --texts <scale.jsonl> --out <dir> [--arm fewshot]
    python -m engine.intent.llm_moat compare --out <dir> --pred-dir reports/intent/run1 --report <json>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from engine.experiments import selective as SEL

ROOT = Path(__file__).resolve().parents[2]
TASKS = ["needs_action", "broad_intent", "sentiment", "urgency"]
CLASSES = {
    "needs_action": ["yes", "no"],
    "broad_intent": ["product_issue", "delivery", "service", "praise", "other"],
    "sentiment": ["positive", "negative", "neutral", "mixed"],
    "urgency": ["low", "medium", "high"],
}
PROMPT = """You label one customer review for a merchant. Answer with JSON only.
Fields (each is an object {{"label": ..., "confidence": 0-100}}):
- needs_action: "yes" if the merchant should do something (fix, reply, refund, investigate), else "no".
- broad_intent: one of product_issue, delivery, service, praise, other.
- sentiment: one of positive, negative, neutral, mixed.
- urgency: one of low, medium, high ("high" only for safety, health or legal risk or an imminent loss).
"confidence" is your honest probability (0-100) that the label is what a careful human panel would choose.
Review: {text}
JSON:"""
FEWSHOT_HEADER = "Worked examples (from labelled training data):\n"
URL = "https://api.groq.com/openai/v1/chat/completions"
TPM_BUDGET = 6500  # of 8,000 TPM, headroom for the other jobs on the org
# U4d is paused until this comparison finishes (GG decision); prod keeps ~20K of the 200K pool.
DAILY_BUDGET = int(os.environ.get("MOAT_DAILY_BUDGET", "180000"))


def ask(key: str, model: str, text: str, shots: str = "") -> tuple[dict | None, int]:
    import httpx

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": PROMPT.replace("Review: {text}", shots + "Review: {text}").format(
                    text=text[:1500]
                ),
            }
        ],
        "temperature": 0,
        "seed": 42,
        "response_format": {"type": "json_object"},
        "max_tokens": 600,
    }
    for attempt in range(5):
        try:
            r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
        except httpx.TransportError:
            time.sleep(5 * 2**attempt)
            continue
        if r.status_code == 429:
            time.sleep(min(60, 5 * 2**attempt))
            continue
        r.raise_for_status()
        j = r.json()
        used = int(j.get("usage", {}).get("total_tokens", 0))
        try:
            return json.loads(j["choices"][0]["message"]["content"]), used
        except (ValueError, KeyError, IndexError):
            return None, used
    return None, 0


def sealed_ids() -> list[str]:
    from engine.labelling.leakage_ledger import exclusions

    excl = exclusions()
    scale = json.loads((ROOT / "reports/labelling/scale/manifest.json").read_text(encoding="utf-8"))
    return [i["id"] for i in scale["items"] if i["split"] == "test" and i["sha"] not in excl]


def pick_shots(texts: Path, n_max: int = 6) -> list[tuple[str, str, dict]]:
    """Training-only worked examples: greedy cover of task/class pairs, deterministic."""
    import random

    labels = json.loads((ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
    tr = labels["labels"]["train"]
    rows = [json.loads(ln) for ln in texts.open(encoding="utf-8")]
    pool = [r for r in rows if r["split"] == "train" and len(r["text"]) <= 220]
    pool = [r for r in pool if all(r["id"] in tr[t] for t in TASKS)]
    random.Random(42).shuffle(pool)
    seen: set[tuple[str, str]] = set()
    out = []
    for r in pool:
        gold = {t: tr[t][r["id"]] for t in TASKS}
        new = set(gold.items()) - seen
        if new and len(out) < n_max:
            seen |= new
            out.append((r["id"], r["text"], gold))
    return out


def shots_text(shots: list[tuple[str, str, dict]]) -> str:
    parts = [FEWSHOT_HEADER]
    for _, text, gold in shots:
        ans = {t: {"label": gold[t], "confidence": 90} for t in TASKS}
        parts.append(f"Review: {text}\nJSON: {json.dumps(ans)}\n")
    return "".join(parts) + "\n"


def decide(rows: list[dict], enc: dict[str, dict], gold: dict, n: int = 300) -> dict:
    """Interim rule at n items: per task, CI of coverage@0.95-precision LLM vs encoder; decided if disjoint."""
    out = {}
    first = {r["id"] for r in rows[:n]}
    for task in TASKS:
        cc, kk = llm_arrays(rows[:n], gold[task], task)
        ll = SEL.summarize(cc, kk, 300)["coverage_at_precision_0.95_ci95"]
        c, k = enc_arrays(enc[task], gold[task], first)
        el = SEL.summarize(c, k, 300)["coverage_at_precision_0.95_ci95"]
        out[task] = {
            "llm_ci": ll,
            "encoder_ci": el,
            "decided": bool(ll[1] < el[0] or el[1] < ll[0]),
            "n": n,
        }
    return out


def run(model: str, texts: Path, out: Path, arm: str = "zeroshot") -> int:
    # The gitignored benchmark env file lives in the main checkout, not in worktrees.
    sys.path.insert(0, os.environ.get("REVIEW_IQ_MAIN_CHECKOUT", str(ROOT)))
    from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key

    key = load_benchmark_groq_key()
    out.mkdir(parents=True, exist_ok=True)
    stem = model.replace("/", "_") + ("" if arm == "zeroshot" else f"__{arm}")
    f = out / f"{stem}.jsonl"
    shots = ""
    if arm == "fewshot":
        picked = pick_shots(texts)
        (out / "fewshot_ids.json").write_text(json.dumps([p[0] for p in picked]) + "\n")
        shots = shots_text(picked)
    labels = json.loads((ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
    gold = labels["labels"]["test"]
    enc = {
        t: json.loads((ROOT / f"reports/intent/run1/pred_{t}.json").read_text(encoding="utf-8"))
        for t in TASKS
    }
    decided_at_300 = None
    done = {json.loads(ln)["id"] for ln in f.open(encoding="utf-8")} if f.exists() else set()
    text_of = {}
    for ln in texts.open(encoding="utf-8"):
        d = json.loads(ln)
        text_of[d["id"]] = d["text"]
    day_tokens, window = 0, []
    with f.open("a", encoding="utf-8") as w:
        for i in sealed_ids():
            if i in done:
                continue
            if day_tokens >= DAILY_BUDGET:
                print(
                    f"daily token budget reached at {day_tokens}; resume after the 00:00 UTC reset"
                )
                return 2
            now = time.time()
            window = [(t, n) for t, n in window if now - t < 60]
            if sum(n for _, n in window) > TPM_BUDGET:
                time.sleep(max(1.0, 60 - (now - window[0][0])))
            ans, used = ask(key, model, text_of[i], shots)
            window.append((time.time(), used))
            day_tokens += used
            w.write(json.dumps({"id": i, "ans": ans, "tokens": used}) + "\n")
            w.flush()
            done.add(i)
            if len(done) == 300 and decided_at_300 is None:
                rows = [json.loads(ln) for ln in f.open(encoding="utf-8")]
                decided_at_300 = decide(rows, enc, gold)
                (out / f"{stem}__interim300.json").write_text(
                    json.dumps(decided_at_300, indent=1) + "\n"
                )
                if all(v["decided"] for v in decided_at_300.values()):
                    print("stopping rule fired for all four tasks at n=300")
                    return 0
    print(f"complete; tokens this invocation {day_tokens}")
    return 0


def llm_arrays(rows: list[dict], gold: dict[str, str], task: str) -> tuple[np.ndarray, np.ndarray]:
    correct, conf = [], []
    for r in rows:
        if r["id"] not in gold:
            continue
        a = (r["ans"] or {}).get(task) or {}
        lab = str(a.get("label", "")).strip().lower()
        try:
            c = float(a.get("confidence", 0)) / 100
        except (TypeError, ValueError):
            c = 0.0
        ok = lab in CLASSES[task] and lab == gold[r["id"]]
        correct.append(ok)
        conf.append(c if lab in CLASSES[task] else 0.0)
    return np.array(correct, dtype=bool), np.array(conf)


def enc_arrays(pred: dict, gold: dict[str, str], keep: set[str]) -> tuple[np.ndarray, np.ndarray]:
    classes = pred["classes"]
    sp = pred["splits"]["test"]
    probs = np.array(sp["probs"]).reshape(len(sp["ids"]), len(classes))
    sel = [j for j, i in enumerate(sp["ids"]) if i in gold and i in keep]
    p = probs[sel]
    y = np.array([classes.index(gold[sp["ids"][j]]) for j in sel])
    return p.argmax(1) == y, p.max(1)


def compare(out: Path, pred_dir: Path, report: Path) -> None:
    labels = json.loads((ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
    keep = set(sealed_ids())
    res: dict = {"n_sealed_after_ledger_exclusions": len(keep), "tasks": {}}
    for task in TASKS:
        gold = labels["labels"]["test"][task]
        pred = json.loads((pred_dir / f"pred_{task}.json").read_text(encoding="utf-8"))
        c, k = enc_arrays(pred, gold, keep)
        entry = {"encoder": SEL.summarize(c, k), "llm": {}}
        for f in sorted(out.glob("*.jsonl")):
            rows = [json.loads(ln) for ln in f.open(encoding="utf-8")]
            cc, kk = llm_arrays(rows, gold, task)
            entry["llm"][f.stem] = SEL.summarize(cc, kk) if len(cc) else {"n": 0}
        res["tasks"][task] = entry
    report.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "compare"])
    ap.add_argument("--model")
    ap.add_argument("--arm", choices=["zeroshot", "fewshot"], default="zeroshot")
    ap.add_argument("--texts", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pred-dir", type=Path)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    if a.cmd == "run":
        return run(a.model, a.texts, a.out, a.arm)
    compare(a.out, a.pred_dir, a.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
