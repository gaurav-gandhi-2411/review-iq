"""E2g: zero-shot and retrieval-few-shot LLM baseline on the dedicated Groq org.

Usage: python -m engine.experiments.llm_baseline --dataset banking77 --model openai/gpt-oss-20b --n 60 --out reports/engine/llm_banking77_20b.json

The key is read from Secret Manager in-process and never printed. Public benchmark text only (no
customer data). Respects the 100K tokens per model per UTC day ceiling: the run stops when the
running total reaches --token-budget and reports how many items it completed.
"""

from __future__ import annotations

import argparse
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
    p = "Classify the user message into exactly one intent. Answer with the intent label only.\n"
    p += "Intents: " + ", ".join(labels) + "\n"
    if shots:
        p += "Examples:\n" + "\n".join(f'"{s.text}" -> {s.label}' for s in shots) + "\n"
    return p + f'Message: "{text}"\nIntent:'


def _parse(ans: str, labels: list[str]) -> str | None:
    a = ans.strip().strip('"').strip()
    if a in labels:
        return a
    hits = [lab for lab in labels if re.search(rf"\b{re.escape(lab)}\b", a)]
    return max(hits, key=len) if hits else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["clinc", "banking77"], required=True)
    ap.add_argument("--model", default="openai/gpt-oss-20b")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--shots", type=int, default=0, help="retrieved nearest training examples")
    ap.add_argument("--token-budget", type=int, default=90_000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    sp = D.load_clinc() if a.dataset == "clinc" else D.load_banking77()
    test = D.stratified_subsample(sp.test, a.n)
    labels = sp.labels
    idx = {lab: i for i, lab in enumerate(labels)}
    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True).fit([e.text for e in sp.train])
    xtr = vec.transform([e.text for e in sp.train])
    key = _key()
    used_in = used_out = 0
    ys, ps, lat, unparsed = [], [], [], 0
    for e in test:
        if used_in + used_out > a.token_budget:
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
        for _ in range(4):
            try:
                r = httpx.post(
                    GROQ, headers={"Authorization": "Bearer " + key}, json=body, timeout=90
                )
            except (
                httpx.TransportError
            ) as e:  # connection reset etc.: back off, keep partial results
                print("transport error, retrying:", type(e).__name__)
                time.sleep(5)
                continue
            if r.status_code == 429:
                time.sleep(float(r.headers.get("retry-after", 8)) + 1)
                continue
            break
        lat.append(time.perf_counter() - t0)
        if r is None or r.status_code != 200:
            print(
                "stop:",
                getattr(r, "status_code", None),
                (r.text[:120] if r else "").replace(key, "<k>"),
            )
            break
        j = r.json()
        used_in += j["usage"]["prompt_tokens"]
        used_out += j["usage"]["completion_tokens"]
        pred = _parse(j["choices"][0]["message"].get("content") or "", labels)
        unparsed += pred is None
        ys.append(idx[e.label])
        ps.append(idx[pred] if pred else -1)
    y, p = np.array(ys), np.array(ps)
    done = len(y)
    pin, pout = PRICE.get(a.model, (None, None))
    res = {
        "commit": _git_sha(), "dataset": a.dataset, "model": a.model, "shots": a.shots,
        "n_completed": done, "unparsed": int(unparsed), "tokens_in": used_in, "tokens_out": used_out,
        "macro_f1_on_sample": round(M.macro_f1(y, p), 4),  # labels absent from sample score 0 by design
        "accuracy": round(M.accuracy(y, p), 4),
        "accuracy_ci95": [round(v, 4) for v in M.bootstrap_ci(y, p, lambda a_, b_: M.accuracy(a_, b_))],
        "latency_p50_s": round(float(np.percentile(lat, 50)), 2),
        "latency_p95_s": round(float(np.percentile(lat, 95)), 2),
        "usd_per_1k_messages_at_published_rate": (
            round((used_in * pin + used_out * pout) / done / 1e6 * 1000, 4) if pin and done else None
        ),
    }  # fmt: skip
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
