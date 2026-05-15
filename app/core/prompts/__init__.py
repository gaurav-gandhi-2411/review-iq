"""Versioned, language-branched prompt registry for Review IQ.

All prompts produce output conforming to the same ReviewExtractionLLMOutput schema.
Only the instructions and examples vary by language.
"""

from __future__ import annotations

PROMPT_VERSION = "v2.0"


def build_prompt(wrapped_review: str, language: str = "en") -> str:
    """Select and build the extraction prompt for the given language.

    Args:
        wrapped_review: Review text wrapped in <review> delimiters
            by :func:`app.core.sanitize.wrap_for_llm`.
        language: Detected language code — "en", "hi-en", "hi", or "other".
            Unrecognised codes fall back to English prompt.

    Returns:
        Formatted prompt string ready to send to the LLM.
    """
    if language == "hi":
        from app.core.prompts.hi import build_prompt as _build
    elif language == "hi-en":
        from app.core.prompts.hi_en import build_prompt as _build
    else:
        from app.core.prompts.en import build_prompt as _build
    return _build(wrapped_review)
