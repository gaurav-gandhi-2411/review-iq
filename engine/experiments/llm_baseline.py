"""E2g: zero-shot and retrieval-few-shot LLM baseline on the dedicated Groq org.

Usage: python -m engine.experiments.llm_baseline --dataset banking77 --model openai/gpt-oss-120b \
           --n 300 --out reports/engine/e2/llm_banking77_120b_0shot.json

The key is read from Secret Manager in-process and never printed. Public benchmark text only (no
customer data).

RESUMABLE ACROSS UTC DAYS. The output file holds one record per classified item. A re-run with the
same --out continues down the same deterministic, label-balanced item order (stratified_subsample is
prefix-stable, so a larger --n only appends) and stops when either --token-budget for THIS invocation
or the 100K per-model per-UTC-day ceiling (counted from the records already in the file, by date) would
be exceeded. The summary is recomputed over all records.

Every record carries the item's index into the dataset's test split (so it can be paired with the
fine-tuned model's prediction for the same item), the predicted label, a verbalised confidence
(0-100; the model is asked to state it; ties are common and handled by selective.py), and token use.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from engine import data as D
from engine import metrics as M
from engine.experiments.run import _git_sha

GROQ = "https://api.groq.com/openai/v1/chat/completions"
DAY_CEILING = 100_000  # tokens per model per UTC day (S20 policy: half of the BELIEVED 200K pool)
PRICE = {  # USD per 1M tokens (in, out): app/core/pricing.py, verified 2026-09-10
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
}


def _key() -> str:
    return subprocess.run(
        ["gcloud.cmd", "secrets", "versions", "access", "latest", "--secret=groq-api-key-samidha",
         "--project=reviewiq-prod-260813", "--account=gaurav.gandhi1129@gmail.com"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


def _prompt(text: str, labels: list[str], shots: list[D.Example]) -> str:
    p = "Classify the user message into exactly one intent.\n"
    p += "Intents: " + ", ".join(labels) + "\n"
    if shots:
        p += "Examples:\n" + "\n".join(f'"{s.text}" -> {s.label}' for s in shots) + "\n"
    p += f'Message: "{text}"\n'
    p += "Answer on ONE line as: <intent> | <confidence>, where confidence is an integer 0-100.\nAnswer:"
    return p


def _parse(ans: str, labels: list[str]) -> tuple[str | None, int | None]:
    head, _, tail = ans.strip().partition("|")
    a = head.strip().strip('"').strip()
    label = a if a in labels else None
    if label is None:
        hits = [lab for lab in labels if re.search(rf"\b{re.escape(lab)}\b", a)]
        label = max(hits, key=len) if hits else None
    m = re.search(r"\d{1,3}", tail)
    conf = min(100, int(m.group())) if m else None
    return label, conf


def _summary(records: list[dict], a: argparse.Namespace, labels: list[str]) -> dict:
    idx = {lab: i for i, lab in enumerate(labels)}
    y = np.array([idx[r["label"]] for r in records])
    p = np.array([idx[r["pred"]] if r["pred"] else -1 for r in records])
    lat = [r["latency_s"] for r in records]
    tin = sum(r["tokens_in"] for r in records)
    tout = sum(r["tokens_out"] for r in records)
    pin, pout = PRICE.get(a.model, (None, None))
    done = len(records)
    return {
        "commit": _git_sha(), "dataset": a.dataset, "model": a.model, "shots": a.shots,
        "n_completed": done, "unparsed": int(sum(r["pred"] is None for r in records)),
        "missing_confidence": int(sum(r["confidence"] is None for r in records)),
        "tokens_in": tin, "tokens_out": tout,
        "tokens_by_utc_day": _by_day(records),
        "macro_f1_on_sample": round(M.macro_f1(y, p), 4),  # classes absent from the sample are skipped
        "accuracy": round(M.accuracy(y, p), 4),
        "accuracy_ci95": [round(v, 4) for v in M.bootstrap_ci(y, p, lambda a_, b_: M.accuracy(a_, b_))],
        "latency_p50_s": round(float(np.percentile(lat, 50)), 2),
        "latency_p95_s": round(float(np.percentile(lat, 95)), 2),
        "usd_per_1k_messages_at_published_rate": (
            round((tin * pin + tout * pout) / done / 1e6 * 1000, 4) if pin and done else None
        ),
    }  # fmt: skip


def _by_day(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        out[r["utc_date"]] = out.get(r["utc_date"], 0) + r["tokens_in"] + r["tokens_out"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["clinc", "banking77"], required=True)
    ap.add_argument("--model", default="openai/gpt-oss-20b")
    ap.add_argument("--n", type=int, default=60, help="total items wanted (prefix-stable order)")
    ap.add_argument("--shots", type=int, default=0, help="retrieved nearest training examples")
    ap.add_argument("--token-budget", type=int, default=90_000, help="tokens for THIS invocation")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    sp = D.load_clinc() if a.dataset == "clinc" else D.load_banking77()
    first = {}
    for i, e in enumerate(sp.test):
        first.setdefault((e.text, e.label), i)
    order = D.stratified_subsample(sp.test, a.n)
    labels = sp.labels
    out = Path(a.out)
    records: list[dict] = []
    if out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        if (old.get("model"), old.get("dataset"), old.get("shots")) != (
            a.model,
            a.dataset,
            a.shots,
        ):
            raise SystemExit(
                f"{out} holds a different run (model/dataset/shots); use another --out"
            )
        records = old["records"]
    done_idx = {r["test_idx"] for r in records}
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    used_today = _by_day(records).get(today, 0)

    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit([e.text for e in sp.train])
    xtr = vec.transform([e.text for e in sp.train])
    key = _key()
    spent = 0
    for e in order:
        ti = first[(e.text, e.label)]
        if ti in done_idx:
            continue
        # stop before a call that could cross either ceiling (3K = largest call seen so far)
        if spent + 3000 > a.token_budget or used_today + spent + 3000 > DAY_CEILING:
            print("stopping: budget reached", {"spent": spent, "used_today_before": used_today})
            break
        shots: list[D.Example] = []
        if a.shots:
            sims = (xtr @ vec.transform([e.text]).T).toarray().ravel()
            shots = [sp.train[i] for i in np.argsort(-sims)[: a.shots]]
        body = {
            "model": a.model,
            "messages": [{"role": "user", "content": _prompt(e.text, labels, shots)}],
            "temperature": 0, "max_tokens": 200, "reasoning_effort": "low",
        }  # fmt: skip
        t0 = time.perf_counter()
        r = None
        for attempt in range(6):
            try:
                r = httpx.post(
                    GROQ, headers={"Authorization": "Bearer " + key}, json=body, timeout=90
                )
            except httpx.TransportError as ex:  # connection reset etc.: back off, keep progress
                print("transport error, retrying:", type(ex).__name__)
                time.sleep(5)
                continue
            if r.status_code == 429:
                time.sleep(float(r.headers.get("retry-after", 8)) + 1)
                continue
            if r.status_code in (
                500,
                502,
                503,
                504,
            ):  # e.g. 'model is over capacity': back off, do not stop the run
                time.sleep(15 * (attempt + 1))
                continue
            break
        lat = time.perf_counter() - t0
        if r is None or r.status_code != 200:
            print(
                "stop:",
                getattr(r, "status_code", None),
                (r.text[:120] if r else "").replace(key, "<k>"),
            )
            break
        j = r.json()
        pred, conf = _parse(j["choices"][0]["message"].get("content") or "", labels)
        tin, tout = j["usage"]["prompt_tokens"], j["usage"]["completion_tokens"]
        spent += tin + tout
        records.append({
            "test_idx": ti, "label": e.label, "pred": pred, "confidence": conf,
            "correct": pred == e.label, "tokens_in": tin, "tokens_out": tout,
            "latency_s": round(lat, 3), "utc_date": today,
        })  # fmt: skip
        out.parent.mkdir(parents=True, exist_ok=True)
        # persist after every item: a crash or a 429 wall never loses the quota already spent
        out.write_text(
            json.dumps({**_summary(records, a, labels), "records": records}), encoding="utf-8"
        )
    if not records:
        raise SystemExit("no items classified")
    res = _summary(records, a, labels)
    out.write_text(json.dumps({**res, "records": records}), encoding="utf-8")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
