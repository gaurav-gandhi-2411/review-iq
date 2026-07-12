"""review-iq system adapter — uses the actual production extraction pipeline.

Maps ReviewExtractionLLMOutput fields to benchmark labels:
  sentiment → SENT  (mixed → neutral, documented)
  urgency   → URG   (direct)
  language  → LANG  (normalised to en / hi-en / hi)

Uses the app's GroqProvider with cassettes at benchmark/cassettes/review_iq_cassettes.json.
The cassette path is overridden at import time so no eval/cassettes data is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Override cassette path BEFORE importing GroqProvider (which reads module-level CASSETTES_PATH)
import app.core.providers.cassette as _eval_cassette_module  # noqa: E402

_eval_cassette_module.CASSETTES_PATH = (
    ROOT / "benchmark" / "cassettes" / "review_iq_cassettes.json"
)

from app.core.prompts import build_prompt  # noqa: E402
from app.core.sanitize import sanitize, wrap_for_llm  # noqa: E402

SYSTEM_ID = "review-iq"
SYSTEM_DESCRIPTION = (
    "review-iq production extraction pipeline: sanitize → language-aware prompt → "
    "tiered Groq routing (llama-3.1-8b-instant for en, llama-3.3-70b-versatile for hi-en). "
    "Full system prompt at app/core/prompts/. "
    "SENT mapping: mixed → neutral (review-iq may return 'mixed'; benchmark is 3-class). "
    "LANG mapping: free string → en/hi-en/hi (unknown → 'en', logged)."
)

# review-iq has a 4th SENT value ('mixed') not in the benchmark's 3-class schema.
# Map mixed → neutral (the nearest concept: both indicate no clear lean).
_SENT_MAP = {"positive": "positive", "neutral": "neutral", "negative": "negative", "mixed": "neutral"}
_LANG_NORM = {"en": "en", "hi-en": "hi-en", "hinglish": "hi-en", "hi": "hi"}


def _map_sent(val: str | None) -> str | None:
    if val is None:
        return None
    return _SENT_MAP.get(str(val).lower())


def _map_lang(val: str | None) -> str:
    if val is None:
        return "en"
    return _LANG_NORM.get(str(val).lower(), "en")


async def predict(text: str, replay_mode: bool) -> dict[str, str | None]:
    """Run review-iq extraction and return {SENT, URG, LANG}."""
    import os  # noqa: PLC0415

    # Control cassette mode for the app's GroqProvider
    os.environ["EVAL_CASSETTE_MODE"] = "replay" if replay_mode else "record"

    from app.core.llm import extract_with_llm  # noqa: PLC0415

    sanitized, _ = sanitize(text)
    wrapped = wrap_for_llm(sanitized)
    # Heuristic language hint for review-iq's prompt router (not the gold label)
    lang_hint = _detect_lang_hint(text)
    user_prompt = build_prompt(wrapped, lang_hint)

    try:
        llm_output, _model, _latency_ms, _tin, _tout, _degraded = await extract_with_llm(
            user_prompt, allow_gemini_fallback=False
        )
        extraction = llm_output.model_dump()
        return {
            "SENT": _map_sent(extraction.get("sentiment")),
            "URG": str(extraction.get("urgency", "low")),
            "LANG": _map_lang(extraction.get("language")),
        }
    except Exception as exc:
        return {"SENT": None, "URG": None, "LANG": None, "_error": str(exc)}


def _detect_lang_hint(text: str) -> str:
    """Prompt-router hint — delegates to the REAL production router so the benchmark
    routes exactly like prod (app/api/v2/extract.py calls detect_language() and the
    hint decides the prompt template). Was previously an independent regex copy that
    drifted from prod (PROMPTS.md records 5 of 9 v0.1 LANG misses were this shim's own
    gap, not prod's). "other" maps to "en" (no "other" prompt template exists).
    NOTE: changing routing invalidates the v0.1 internal benchmark's replay cassettes
    for any fixture whose hint changes — re-record needed before re-running v0.1.
    """
    from app.core.language import detect_language  # noqa: PLC0415

    lang = detect_language(text)
    return "en" if lang == "other" else lang
