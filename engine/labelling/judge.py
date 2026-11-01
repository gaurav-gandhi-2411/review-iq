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
from engine.labelling import prompts_v2 as P2
from engine.labelling import prompts_v3 as P3


def chat(
    base_url: str, model: str, prompt: str, timeout: float = 180.0, num_predict: int = 400
) -> tuple[str, int, int]:
    body = {
        "model": model, "stream": False, "format": "json", "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "seed": 42, "num_predict": num_predict},
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


def _record_v2(base_url: str, model: str, it: dict, mod=P2) -> dict:  # noqa: ANN001 -- prompt module
    """v2/v3: ONE text call (no stars, no mismatch call); one retry with a stricter reminder on a parse failure."""
    tin = tout = 0
    parsed, dropped, retried = None, 0, False
    for attempt in range(2):
        raw, a, b = chat(
            base_url,
            model,
            mod.text_prompt(it["text"], it["category"], retry=attempt == 1),
            num_predict=700,
        )
        tin, tout = tin + a, tout + b
        parsed, dropped = mod.parse_text(raw, it["category"], it["text"])
        if parsed is not None:
            break
        retried = True
    return {
        "text": parsed, "mismatch": None, "tokens_in": tin, "tokens_out": tout, "text_failed": parsed is None,
        "mismatch_failed": False, "aspects_dropped_unverifiable": dropped, "retried": retried,
        "prompt_version": mod.PROMPT_VERSION, "prompt_hash": mod.prompt_hash(),
    }  # fmt: skip


def run(items_path: Path, model: str, out: Path, base_url: str, version: str = "v1") -> dict:
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
    assert version in ("v1", "v2", "v3")
    fails = 0
    t0 = time.time()
    with out.open("a", encoding="utf-8") as f:
        for it in items:
            if it["id"] in done:
                continue
            if version in ("v2", "v3"):
                rec = _record_v2(base_url, model, it, P3 if version == "v3" else P2)
                fails += rec["text_failed"]
                f.write(json.dumps({
                    "id": it["id"], "stratum": it["stratum"], "category": it["category"], "judge": out.stem,
                    "model": model, **rec,
                }) + "\n")  # fmt: skip
                f.flush()
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
    ap.add_argument("--prompt-version", choices=["v1", "v2", "v3"], default="v1")
    a = ap.parse_args()
    print(json.dumps(run(Path(a.items), a.model, Path(a.out), a.base_url, a.prompt_version)))


if __name__ == "__main__":
    main()
