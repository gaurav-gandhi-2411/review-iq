"""Local-inference runner for the moat comparison's gpt-oss-20b arm (spec Amendment 7, arm `gpt-oss-20b-local`).

Same PROMPT, row schema `{id, ans, tokens}`, sealed order, scoring and n=300 interim rule as `llm_moat.py`; the only difference
is the endpoint: an OpenAI-compatible Ollama server (one per GPU on Kaggle). The Groq arms are untouched. Review text is read
from a private items file (`{id, text}` lines, in `sealed_ids()` order) and never written to any output.

    python -m engine.intent.llm_moat_local run --items items.jsonl --out out_dir --urls http://127.0.0.1:11434,http://127.0.0.1:11435
    python -m engine.intent.llm_moat_local merge --shards out_dir/shards --out D:/ml-cache/s23/llm_local/openai_gpt-oss-20b__local.jsonl

Runtime errors (connection, timeout, 5xx, any non-200) are retried and then raise InfraError: they are never recorded as answers.
An empty or unparseable completion is an answer: ans=None, which `llm_moat.llm_arrays` scores as wrong at confidence 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from engine.intent import llm_moat as M

MODEL = "gpt-oss:20b"
STEM = "openai_gpt-oss-20b__local"
N_INTERIM = 300


def build_body(model: str, text: str, json_mode: bool = True) -> dict:
    """Identical to the request body of `llm_moat.ask` (zero-shot) apart from the model name."""
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": M.PROMPT.format(text=text[:1500]),
            }
        ],
        "temperature": 0,
        "seed": 42,
        "max_tokens": 600,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def parse_content(content: str | None) -> dict | None:
    """json.loads, else the outermost {...} substring once, else None (unparseable)."""
    if not content:
        return None
    for cand in (content, content[content.find("{") : content.rfind("}") + 1]):
        try:
            out = json.loads(cand)
        except ValueError:
            continue
        return out if isinstance(out, dict) else None
    return None


def ask_local(
    base_url: str,
    model: str,
    text: str,
    json_mode: bool = True,
    retries: int = 5,
    sleep: float = 5.0,
) -> tuple[dict | None, int]:
    import httpx

    body = build_body(model, text, json_mode)
    last = "no attempt"
    for attempt in range(retries):
        try:
            r = httpx.post(f"{base_url}/v1/chat/completions", json=body, timeout=600)
        except httpx.TransportError as e:
            last = f"transport: {type(e).__name__}"
            time.sleep(sleep * 2**attempt)
            continue
        if r.status_code != 200:
            last = f"http {r.status_code}"
            time.sleep(sleep * 2**attempt)
            continue
        try:
            j = r.json()
            content = j["choices"][0]["message"].get("content")
            used = int(j.get("usage", {}).get("total_tokens", 0))
        except (ValueError, KeyError, IndexError, TypeError):
            last = "malformed envelope"
            time.sleep(sleep * 2**attempt)
            continue
        return parse_content(content), used
    raise M.InfraError(f"local runtime failure after {retries} attempts ({last})")


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(ln)["id"] for ln in path.open(encoding="utf-8") if ln.strip()}


def read_items(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.open(encoding="utf-8") if ln.strip()]


def merge(shard_dir: Path, ids: list[str]) -> list[dict]:
    """All shard rows, ordered as `ids` (sealed order); rows for ids not in `ids` are dropped."""
    by_id: dict[str, dict] = {}
    for f in sorted(shard_dir.glob("*.jsonl")):
        for ln in f.open(encoding="utf-8"):
            if ln.strip():
                r = json.loads(ln)
                by_id[r["id"]] = {"id": r["id"], "ans": r["ans"], "tokens": r["tokens"]}
    return [by_id[i] for i in ids if i in by_id]


def load_decide_inputs() -> tuple[dict, dict]:
    labels = json.loads((M.ROOT / "reports/intent/labels/labels.json").read_text(encoding="utf-8"))
    enc = {
        t: json.loads((M.ROOT / f"reports/intent/run1/pred_{t}.json").read_text(encoding="utf-8"))
        for t in M.TASKS
    }
    return enc, labels["labels"]["test"]


def _work(
    shard: int, url: str, items: list[dict], shard_dir: Path, model: str, json_mode: bool,
    deadline: float, stats: dict,
) -> None:  # fmt: skip
    f = shard_dir / f"{STEM}__shard{shard}.jsonl"
    done = done_ids(f)
    st = stats.setdefault(shard, {"n": 0, "tokens": 0, "elapsed_s": 0.0, "unparseable": 0})
    with f.open("a", encoding="utf-8") as w:
        for it in items:
            if it["id"] in done:
                continue
            if time.time() > deadline:
                stats["budget_hit"] = True
                return
            t0 = time.time()
            ans, used = ask_local(
                url, model, it["text"], json_mode
            )  # InfraError propagates: not recorded
            st["elapsed_s"] += time.time() - t0
            st["n"] += 1
            st["tokens"] += used
            st["unparseable"] += ans is None
            w.write(json.dumps({"id": it["id"], "ans": ans, "tokens": used}) + "\n")
            w.flush()


def _phase(
    items: list[dict], urls: list[str], shard_dir: Path, model: str, json_mode: bool,
    deadline: float, stats: dict,
) -> None:  # fmt: skip
    errs: list[BaseException] = []

    def target(k: int) -> None:
        try:
            _work(k, urls[k], items[k :: len(urls)], shard_dir, model, json_mode, deadline, stats)
        except BaseException as e:  # noqa: BLE001 (surface worker failures to the caller)
            errs.append(e)

    ts = [threading.Thread(target=target, args=(k,)) for k in range(len(urls))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if errs:
        raise errs[0]


def run_local(
    items: list[dict], urls: list[str], out: Path, model: str = MODEL, json_mode: bool = True,
    budget_s: float = 1e12, decide_fn: Callable[[list[dict]], dict] | None = None, n: int = N_INTERIM,
    limit: int | None = None,
) -> dict:  # fmt: skip
    """Phase 1 = first `n` items, then the interim rule via `decide_fn`, then the rest unless all tasks are decided."""
    shard_dir = out / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + budget_s
    stats: dict = {}
    ids = [i["id"] for i in items]
    if limit is not None:
        _phase(items[:limit], urls, shard_dir, model, json_mode, deadline, stats)
        return {"stats": stats, "interim": None, "complete": False}
    _phase(items[:n], urls, shard_dir, model, json_mode, deadline, stats)
    interim = None
    rows = merge(shard_dir, ids)
    if len(rows) >= n and decide_fn is not None:
        interim_path = out / f"{STEM}__interim{n}.json"
        if interim_path.exists():
            interim = json.loads(interim_path.read_text(encoding="utf-8"))
        else:
            interim = decide_fn(rows)
            interim_path.write_text(json.dumps(interim, indent=1) + "\n", encoding="utf-8")
        if all(v["decided"] for v in interim.values()):
            return {"stats": stats, "interim": interim, "complete": False, "rule_fired_all": True}
    if len(rows) >= n or decide_fn is None:
        _phase(items[n:], urls, shard_dir, model, json_mode, deadline, stats)
    complete = len(merge(shard_dir, ids)) == len(ids)
    return {"stats": stats, "interim": interim, "complete": complete}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "merge"])
    ap.add_argument("--items", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--urls", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--no-json-mode", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--budget-h", type=float, default=8.0)
    ap.add_argument("--shards", type=Path)
    a = ap.parse_args()
    if a.cmd == "merge":
        ids = M.sealed_ids()
        rows = merge(a.shards, ids)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        print(f"merged {len(rows)} of {len(ids)} sealed ids -> {a.out.name}")
        return 0
    items = read_items(a.items)
    decide_fn: Callable[[list[dict]], dict] | None = None
    if a.limit is None:
        enc, gold = load_decide_inputs()
        decide_fn = lambda rows: M.decide(rows, enc, gold)  # noqa: E731

    res = run_local(
        items, a.urls.split(","), a.out, a.model, not a.no_json_mode, a.budget_h * 3600, decide_fn,
        limit=a.limit,
    )  # fmt: skip
    (a.out / "run_result.json").write_text(
        json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
