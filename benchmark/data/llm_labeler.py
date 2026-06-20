"""LLM labeler — generates SENT/URG/LANG gold labels for benchmark candidates.

Model: llama-3.3-70b-versatile (Groq, free tier)
Output: benchmark/dataset/gold.jsonl

Labels are explicitly marked LLM-generated (internal benchmark only).
They are NOT independent ground truth — see REPORT.md for what scores mean.

Usage:
    uv run python benchmark/data/llm_labeler.py [--replay]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from groq import AsyncGroq  # noqa: E402

from benchmark._cassette import BenchCassette, make_key  # noqa: E402

LABELER_MODEL = "llama-3.3-70b-versatile"

LABELING_SYSTEM_PROMPT = """\
You are a review classifier for an internal benchmark.
Return ONLY valid JSON. No markdown, no commentary."""

# Prompt text is hashed and stored in gold.jsonl for reproducibility.
LABELING_USER_TEMPLATE = """\
Classify this customer review on three dimensions.

SENT — overall sentiment:
  positive = overall tone is appreciative / satisfied (may have minor complaints)
  neutral  = mixed or factual, no clear positive or negative lean
  negative = overall tone is dissatisfied / critical

URG — urgency for seller response:
  high   = explicit refund/return/replacement demand, OR health/safety risk
  medium = quality defect or significant complaint, no explicit escalation request
  low    = positive review, neutral feedback, praise, or minor quibble

LANG — language / code-mix:
  en    = English only (Latin script, no Hindi words)
  hi-en = Hinglish — Latin-script mix of Hindi and English words
  hi    = Hindi in Devanagari script

Review text:
<review>
{text}
</review>

Return JSON with exactly these keys: {{"SENT": "...", "URG": "...", "LANG": "..."}}"""

_PROMPT_SHA256 = hashlib.sha256(
    (LABELING_SYSTEM_PROMPT + "\n---\n" + LABELING_USER_TEMPLATE).encode("utf-8")
).hexdigest()

CASSETTE_PATH = ROOT / "benchmark" / "cassettes" / "labeler_cassettes.json"
CANDIDATES_PATH = ROOT / "benchmark" / "dataset" / "candidates_for_review.jsonl"
GOLD_PATH = ROOT / "benchmark" / "dataset" / "gold.jsonl"

VALID_SENT = {"positive", "neutral", "negative"}
VALID_URG = {"low", "medium", "high"}
VALID_LANG = {"en", "hi-en", "hi"}


def _parse_labels(raw: str) -> dict[str, str] | None:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    sent = str(obj.get("SENT", "")).lower().strip()
    urg = str(obj.get("URG", "")).lower().strip()
    lang = str(obj.get("LANG", "")).lower().strip()
    if sent not in VALID_SENT or urg not in VALID_URG or lang not in VALID_LANG:
        return None
    return {"SENT": sent, "URG": urg, "LANG": lang}


async def _label_one(
    text: str,
    cassette: BenchCassette,
    groq_client: AsyncGroq,
    replay_mode: bool,
) -> tuple[dict[str, str], int, int]:
    user_prompt = LABELING_USER_TEMPLATE.format(text=text)
    key = make_key(LABELER_MODEL, LABELING_SYSTEM_PROMPT, user_prompt)

    cached = cassette.get(key)
    if cached is not None:
        raw, tin, tout = cached
        labels = _parse_labels(raw)
        if labels:
            return labels, tin, tout

    if replay_mode:
        raise RuntimeError(f"No cassette entry for key {key[:16]}...")

    response = await groq_client.chat.completions.create(
        model=LABELER_MODEL,
        messages=[
            {"role": "system", "content": LABELING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        timeout=30,
    )
    raw = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    tin = getattr(usage, "prompt_tokens", 0) if usage else 0
    tout = getattr(usage, "completion_tokens", 0) if usage else 0

    cassette.put(key, raw, tin, tout)

    labels = _parse_labels(raw)
    if not labels:
        raise ValueError(f"Unparseable label response: {raw!r}")
    return labels, tin, tout


async def run(replay_mode: bool = False) -> None:
    from app.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    cassette = BenchCassette(CASSETTE_PATH)
    groq_client = AsyncGroq(api_key=settings.groq_api_key)

    # Load candidates
    candidates = [
        json.loads(line)
        for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # Load already-labeled IDs
    existing_ids: set[str] = set()
    if GOLD_PATH.exists():
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing_ids.add(json.loads(line)["id"])

    # Renumber: bench-en-NNN, bench-hien-NNN sequentially
    en_idx = 1
    hien_idx = 1
    renumbered: list[tuple[str, dict]] = []
    for cand in candidates:
        sl = cand["slice"]
        if sl == "en":
            new_id = f"bench-en-{en_idx:03d}"
            en_idx += 1
        else:
            new_id = f"bench-hien-{hien_idx:03d}"
            hien_idx += 1
        renumbered.append((new_id, cand))

    pending = [(new_id, cand) for new_id, cand in renumbered if new_id not in existing_ids]
    print(f"Candidates: {len(renumbered)}  Already labeled: {len(existing_ids)}  Pending: {len(pending)}")
    print(f"Model: {LABELER_MODEL}  Cassette: {CASSETTE_PATH}  Replay: {replay_mode}")
    print()

    total_in = total_out = 0
    errors = 0

    with GOLD_PATH.open("a", encoding="utf-8") as fh:
        for new_id, cand in pending:
            text = cand["text"]
            t0 = time.monotonic()
            try:
                labels, tin, tout = await _label_one(text, cassette, groq_client, replay_mode)
            except Exception as exc:
                print(f"  ERROR [{new_id}]: {exc}")
                errors += 1
                continue

            total_in += tin
            total_out += tout
            latency_ms = int((time.monotonic() - t0) * 1000)

            record = {
                "id": new_id,
                "original_id": cand["id"],
                "slice": cand["slice"],
                "domain": cand.get("domain", ""),
                "source": cand.get("source", ""),
                "text": text,
                "char_len": cand.get("char_len", len(text)),
                "rating": cand.get("rating"),
                "gold": labels,
                "labels_source": f"LLM-generated ({LABELER_MODEL}, internal benchmark)",
                "labeling_prompt_sha256": _PROMPT_SHA256,
                "labeling_latency_ms": latency_ms,
                "labeling_tokens_in": tin,
                "labeling_tokens_out": tout,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            cached_flag = "(cached)" if cassette.get(make_key(LABELER_MODEL, LABELING_SYSTEM_PROMPT, LABELING_USER_TEMPLATE.format(text=text))) else "(live)"
            print(f"  [{new_id}] SENT={labels['SENT']}  URG={labels['URG']}  LANG={labels['LANG']}  {latency_ms}ms {cached_flag}")

    print(f"\nLabeling complete. Errors: {errors}")
    print(f"Tokens — in: {total_in:,}  out: {total_out:,}")
    print(f"Gold labels at: {GOLD_PATH}")
    print(f"Cassette entries: {cassette.size()}")

    # Also write the labeling prompt to a file for provenance
    prompt_path = ROOT / "benchmark" / "dataset" / "labeling_prompt.txt"
    prompt_path.write_text(
        f"# Labeling prompt (internal benchmark)\n"
        f"# Model: {LABELER_MODEL}\n"
        f"# SHA256: {_PROMPT_SHA256}\n\n"
        f"## System prompt\n\n{LABELING_SYSTEM_PROMPT}\n\n"
        f"## User template\n\n{LABELING_USER_TEMPLATE}\n",
        encoding="utf-8",
    )
    print(f"Prompt logged: {prompt_path}")


def main() -> None:
    replay_mode = "--replay" in sys.argv
    asyncio.run(run(replay_mode=replay_mode))


if __name__ == "__main__":
    main()
