"""Authenticity scoring prompt — language-aware."""

from __future__ import annotations

_SYSTEM_PROMPT: str = (
    "You are a review-authenticity analyst. "
    "Your job is to assess whether a customer review is genuine or potentially fake/incentivized. "
    "Be conservative: only flag when evidence is clear. "
    "Output a JSON object only."
)

_TASK_EN: str = """\
Score the following customer review for authenticity.

Return ONLY a JSON object with this exact structure:
{
  "score": <float 0.0–1.0 where 1.0 = definitely genuine>,
  "flags": [<zero or more flags from the allowed set>],
  "reasoning": "<one sentence>"
}

Allowed flags (include ONLY when clearly evidenced):
- "incentivized_phrase"   : review mentions receiving the product free, discounts, or compensation
- "rating_text_mismatch"  : star rating strongly contradicts the text sentiment
- "generic_low_info"      : review is vague, generic, or contains almost no specific detail
- "promotional_tone"      : language reads like marketing copy rather than a genuine user experience

Review:
{review_text}"""

_TASK_HI: str = """\
Score the following customer review for authenticity.
Note: this review may be written in Hindi (Devanagari script). Apply the same authenticity criteria.

Return ONLY a JSON object with this exact structure:
{
  "score": <float 0.0–1.0 where 1.0 = definitely genuine>,
  "flags": [<zero or more flags from the allowed set>],
  "reasoning": "<one sentence>"
}

Allowed flags (include ONLY when clearly evidenced):
- "incentivized_phrase"   : review mentions receiving the product free, discounts, or compensation
- "rating_text_mismatch"  : star rating strongly contradicts the text sentiment
- "generic_low_info"      : review is vague, generic, or contains almost no specific detail
- "promotional_tone"      : language reads like marketing copy rather than a genuine user experience

Review:
{review_text}"""

_TASK_HI_EN: str = """\
Score the following customer review for authenticity.
Note: this review may mix Hindi and English (Hinglish). Apply the same authenticity criteria.

Return ONLY a JSON object with this exact structure:
{
  "score": <float 0.0–1.0 where 1.0 = definitely genuine>,
  "flags": [<zero or more flags from the allowed set>],
  "reasoning": "<one sentence>"
}

Allowed flags (include ONLY when clearly evidenced):
- "incentivized_phrase"   : review mentions receiving the product free, discounts, or compensation
- "rating_text_mismatch"  : star rating strongly contradicts the text sentiment
- "generic_low_info"      : review is vague, generic, or contains almost no specific detail
- "promotional_tone"      : language reads like marketing copy rather than a genuine user experience

Review:
{review_text}"""

_TASK_OTHER: str = """\
Score the following customer review for authenticity.
Note: the review language may not be English. Apply the same authenticity criteria regardless.

Return ONLY a JSON object with this exact structure:
{
  "score": <float 0.0–1.0 where 1.0 = definitely genuine>,
  "flags": [<zero or more flags from the allowed set>],
  "reasoning": "<one sentence>"
}

Allowed flags (include ONLY when clearly evidenced):
- "incentivized_phrase"   : review mentions receiving the product free, discounts, or compensation
- "rating_text_mismatch"  : star rating strongly contradicts the text sentiment
- "generic_low_info"      : review is vague, generic, or contains almost no specific detail
- "promotional_tone"      : language reads like marketing copy rather than a genuine user experience

Review:
{review_text}"""

_TASK_BY_LANGUAGE: dict[str, str] = {
    "en": _TASK_EN,
    "hi": _TASK_HI,
    "hi-en": _TASK_HI_EN,
    "other": _TASK_OTHER,
}


def build_authenticity_prompt(review_text: str, language: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given language.

    user_prompt embeds the review text. Falls back to the English task template
    for any unrecognised language code.
    """
    task_template = _TASK_BY_LANGUAGE.get(language, _TASK_EN)
    user_prompt = task_template.format(review_text=review_text)
    return _SYSTEM_PROMPT, user_prompt
