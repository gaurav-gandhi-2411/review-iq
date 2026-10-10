"""Run one LLM judge over the pilot sample through an Ollama server. Resumable, blind, deterministic.

    python -m engine.labelling.judge --items pilot1.jsonl --model llama3.1:8b --out judges/llama31_8b.jsonl \
        [--base-url http://127.0.0.1:11434]

Each output line carries NO review text (ids and labels only), the prompt version and hash, the model name
and token counts, so judge files can be committed. Temperature 0 and a fixed seed; format=json; thinking off.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

from engine.labelling import prompts as P


def chat(base_url: str, model: str, prompt: str, timeout: float = 180.0) -> tuple[str, int, int]:
    body = {
        "model": model, "stream": False, "format": "json", "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "seed": 42, "num_predict": 400},
    }  # fmt: skip
    for attempt in range(4):
        try:
            r = httpx.post(f"{base_url}/api/chat", json=body, timeout=timeout)
            if r.status_code == 200:
                j = r.json()
                return (
                    j["message"]["content"], j.get("prompt_eval_count", 0), j.get("eval_count", 0)
                )  # fmt: skip
        except httpx.TransportError:
            pass
        time.sleep(3 * (attempt + 1))
    return "", 0, 0


def run(items_path: Path, model: str, out: Path, base_url: str) -> dict:
    items = [
        json.loads(line)
        for line in items_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    done = set()
    if out.exists():
        done = {
            json.loads(line)["id"]
            for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    ph = P.prompt_hash()
    fails = 0
    t0 = time.time()
    with out.open("a", encoding="utf-8") as f:
        for it in items:
            if it["id"] in done:
                continue
            raw1, a1, b1 = chat(base_url, model, P.text_prompt(it["text"], it["category"]))
            raw2, a2, b2 = chat(base_url, model, P.mismatch_prompt(it["text"], it["stars"]))
            text, mm = P.parse_text(raw1, it["category"]), P.parse_mismatch(raw2)
            fails += text is None
            f.write(json.dumps({
                "id": it["id"], "stratum": it["stratum"], "category": it["category"], "judge": out.stem,
                "model": model, "prompt_version": P.PROMPT_VERSION, "prompt_hash": ph,
                "text": text, "mismatch": mm, "tokens_in": a1 + a2, "tokens_out": b1 + b2,
                "text_failed": text is None, "mismatch_failed": mm is None,
            }) + "\n")  # fmt: skip
            f.flush()
    return {
        "model": model,
        "items": len(items),
        "text_parse_failures": fails,
        "seconds": round(time.time() - t0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    a = ap.parse_args()
    print(json.dumps(run(Path(a.items), a.model, Path(a.out), a.base_url)))


if __name__ == "__main__":
    main()
