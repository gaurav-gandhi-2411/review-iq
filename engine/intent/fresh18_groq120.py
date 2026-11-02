"""Amendment 18 secondary arm: gpt-oss-120b on the Groq FAILOVER org, on the registered fresh18 subsample of S4 gold-yes only.

    # 1. the registered subsample (ids only), from the fresh18 labels
    PYTHONPATH=. python -m engine.intent.fresh18_eval subsample --labels reports/intent/labels_fresh18/fresh18_labels.json \
        --out reports/intent/fresh18/subsample_120b.json
    # 2. resumable, bounded runs under the failover-key wrapper (the key value is never printed or written)
    PYTHONPATH=. python -m engine.intent.fresh18_groq120 --subsample reports/intent/fresh18/subsample_120b.json \
        --texts D:/ml-cache/s33/fresh18/fresh18.jsonl --out D:/ml-cache/s34/groq120_f18

Same request body as the S32 120b arm (Amendment 15 settings, max_tokens 1500, reasoning low). Every call passes the machine-wide
ledger (`benchmark` project, fail closed) and this module's own trailing-24 h cap of 20,000 tokens (the `benchmark` cap of
triage-iq decision D95): about 48 items a day, so 200 items take about 5 days. Rows: {id, ans, tokens, finish_reason}, the format
`fresh18_eval score --llm120` reads. Review text is read from the private file and never written.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from engine.intent import llm_moat as M
from engine.intent import llm_moat_local as L

MODEL_120B = "openai/gpt-oss-120b"
CAP_24H = 20_000  # tokens per trailing 24 h: the `benchmark` cap in the shared ledger config (triage-iq D95)


def ask_groq(key: str, text: str) -> tuple[dict | None, int, str]:
    """One 120b call with the 20b arm's exact body. -> (answer or None, tokens, finish_reason)."""
    import httpx

    body = L.build_body(MODEL_120B, text)
    for attempt in range(6):
        try:
            r = httpx.post(
                M.URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120
            )
        except httpx.TransportError:
            time.sleep(5 * 2**attempt)
            continue
        if r.status_code == 429:
            time.sleep(min(300, 15 * 2**attempt))
            continue
        if (
            r.status_code == 400 and attempt >= 1
        ):  # e.g. json_validate_failed twice: an unparseable answer
            return None, 0, "json_validate_failed"
        if r.status_code == 400 or r.status_code >= 500:
            time.sleep(5)
            continue
        r.raise_for_status()
        j = r.json()
        ch = j["choices"][0]
        return (
            L.parse_content(ch["message"].get("content")),
            int(j.get("usage", {}).get("total_tokens", 0)),
            str(ch.get("finish_reason")),
        )
    raise M.InfraError("rate limit, 5xx or transport failure after 6 attempts")


def read_texts(path: Path, ids: set[str]) -> dict[str, str]:
    out = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            if r["id"] in ids:
                out[r["id"]] = r["text"]
    return out


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        json.loads(ln)["id"] for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
    }


def run(subsample: list[str], texts_path: Path, out: Path, limit: int | None) -> int:
    key = os.environ.get("GROQ_EVAL_KEY", "")
    if not key:
        raise SystemExit("GROQ_EVAL_KEY is not set (the FAILOVER org key, via the wrapper)")
    # A blocked ledger call raises BudgetWaitTimeout after a short wait, so every invocation stays a bounded foreground command.
    os.environ.setdefault("GROQ_BUDGET_WAIT_S", "60")
    from engine.groq_budget_hook import BudgetWaitTimeout, install

    install()  # machine-wide ledger; fails closed if the ledger module is missing
    L.GEN.update({"max_tokens": 1500, "reasoning_effort": "low"})
    out.mkdir(parents=True, exist_ok=True)
    f, upath = out / "gpt-oss-120b.jsonl", M.usage_path(out, MODEL_120B)
    done = done_ids(f)
    usage = M.read_usage(upath)
    texts = read_texts(texts_path, set(subsample))
    missing = [i for i in subsample if i not in texts]
    if missing:
        raise SystemExit(f"{len(missing)} subsample ids have no text in {texts_path}")
    n_new = 0
    with f.open("a", encoding="utf-8") as w:
        for i in subsample:
            if i in done:
                continue
            if limit is not None and n_new >= limit:
                break
            recent = [n for _, n in usage[-20:] if n > 0]
            need = max(800, int(sum(recent) / len(recent))) if recent else 1500
            wait = M.wait_seconds(usage, need, time.time(), cap=CAP_24H)
            if wait > 0:
                print(
                    json.dumps(
                        {
                            "stopped": "own 24h cap",
                            "wait_h": round(wait / 3600, 2),
                            "done": len(done),
                        }
                    )
                )
                return 4
            try:
                ans, used, finish = ask_groq(key, texts[i])
            except M.InfraError as e:
                print(json.dumps({"stopped": str(e), "done": len(done)}))
                return 3
            except BudgetWaitTimeout:
                print(json.dumps({"stopped": "shared ledger pool", "done": len(done)}))
                return 4
            usage.append((time.time(), used))
            with upath.open("a", encoding="utf-8") as uw:
                uw.write(json.dumps({"ts": usage[-1][0], "tokens": used}) + "\n")
            w.write(
                json.dumps({"id": i, "ans": ans, "tokens": used, "finish_reason": finish}) + "\n"
            )
            w.flush()
            done.add(i)
            n_new += 1
    print(json.dumps({"done": len(done), "new": n_new, "of": len(subsample)}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subsample", type=Path, required=True)
    ap.add_argument("--texts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    sub = json.loads(a.subsample.read_text(encoding="utf-8"))
    ids = sub["ids"] if isinstance(sub, dict) else sub
    return run(list(ids), a.texts, a.out, a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
