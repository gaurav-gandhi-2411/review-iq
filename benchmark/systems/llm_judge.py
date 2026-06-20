"""LLM-judge system — Groq Llama 3.1 8B with a fair direct-classification prompt.

Documented system prompt below. This is a DIFFERENT model and simpler prompt than both
the labeling LLM (3.3 70B) and review-iq's extraction path.

Note on interpretation: since the labeling model is llama-3.3-70b-versatile and this
judge uses llama-3.1-8b-instant (same family, different size), high agreement is partly
expected from shared pretraining. The report flags this explicitly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from groq import AsyncGroq

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark._cassette import BenchCassette, make_key  # noqa: E402

SYSTEM_ID = "llm-judge-llama-3.1-8b"
JUDGE_MODEL = "llama-3.1-8b-instant"

# Deliberately simpler than the labeling prompt — fair prompt for a classification task.
# Does NOT include the detailed rubric from the labeler; just the label names and brief gloss.
JUDGE_SYSTEM_PROMPT = "You are a review classifier. Return ONLY valid JSON, no markdown."

JUDGE_USER_TEMPLATE = """\
Classify this customer review.

SENT: positive / neutral / negative  (overall sentiment)
URG: low / medium / high  (urgency — high = refund/safety demand; medium = defect complaint; low = praise/minor issue)
LANG: en / hi-en / hi  (en = English; hi-en = Hinglish Latin-script mix; hi = Hindi Devanagari)

Review:
<review>
{text}
</review>

Return: {{"SENT": "...", "URG": "...", "LANG": "..."}}"""

JUDGE_CASSETTE_PATH = ROOT / "benchmark" / "cassettes" / "llm_judge_cassettes.json"

VALID_SENT = {"positive", "neutral", "negative"}
VALID_URG = {"low", "medium", "high"}
VALID_LANG = {"en", "hi-en", "hi"}

SYSTEM_DESCRIPTION = (
    f"LLM judge: {JUDGE_MODEL} (Groq, free tier). "
    "Fair direct-classification prompt (see benchmark/systems/llm_judge.py). "
    "DIFFERENT model from labeling LLM (3.3 70B). "
    "Same Llama family — expect moderate correlation with labels."
)


def _parse(raw: str) -> dict[str, str] | None:
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


async def predict(
    text: str,
    groq_client: AsyncGroq,
    cassette: BenchCassette,
    replay_mode: bool,
) -> dict[str, str] | None:
    user_prompt = JUDGE_USER_TEMPLATE.format(text=text)
    key = make_key(JUDGE_MODEL, JUDGE_SYSTEM_PROMPT, user_prompt)

    cached = cassette.get(key)
    if cached is not None:
        raw, _, _ = cached
        return _parse(raw)

    if replay_mode:
        raise RuntimeError(f"LLM-judge: no cassette entry for key {key[:16]}...")

    response = await groq_client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
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
    return _parse(raw)
