"""Hinglish (Roman-script Hindi/English code-mix) extraction prompt — v2.0."""

from __future__ import annotations

_FIELD_DESCRIPTIONS = """
This review is written in Hinglish — a natural code-mix of Hindi (in Roman script) and English
common among Indian consumers. Words like "bahut", "hai", "nahi", "ekdum", "paisa vasool" are Hindi.

IMPORTANT: Output ALL field values in English, regardless of input language.
- Translate/summarize Hindi words in pros, cons, topics, and feature_requests into plain English.
- For product: extract the product name exactly as it appears (often in English within Hinglish text).
- language: always "hi-en" for this prompt.

Field definitions:
- product: Extract the product name/category. Often English words within Hinglish text.
- stars: ONLY if explicitly stated as a number (e.g. "4 star diya", "★★★★"). NULL otherwise.
- stars_inferred: Your holistic 1-5 estimate of satisfaction. Always populate.
- pros: ALL positive points — translate into English short phrases. E.g. "bahut achha sound" → "excellent sound quality".
- cons: ALL negative points — translate into English short phrases.
- buy_again: true if reviewer recommends; false if explicitly says won't buy; null if unclear.
- sentiment: "positive" | "negative" | "neutral" | "mixed".
- topics: English topic words for each pro/con. Use snake_case (e.g. sound_quality, battery, price).
- competitor_mentions: Brand names mentioned. Empty list if none.
- urgency: "high" (safety/return intent/anger), "medium" (frustration), "low" (normal feedback).
- feature_requests: Feature wishes — translate to English. Empty list if none.
- confidence: 0.0–1.0.
"""

_EXAMPLE = """
Example — Hinglish mixed review:
Review: <review>Superb earphone, sound ekdum mast hai yaar. Apple earphone ko competition dega. But battery bahut weak hai. Paisa vasool nahi laga for 2000 rupees.</review>
Output: {"product": "earphone", "stars": null, "stars_inferred": 3, "pros": ["excellent sound quality", "competitive with Apple"], "cons": ["poor battery life", "not value for money"], "buy_again": null, "sentiment": "mixed", "topics": ["sound_quality", "battery", "price", "comparison"], "competitor_mentions": ["Apple"], "urgency": "low", "feature_requests": [], "language": "hi-en", "confidence": 0.88}
"""

_TEMPLATE = """\
Extract structured information from the Hinglish customer review below.
Output all field values in English (translate from Hindi where needed).

{field_descriptions}

{example}

Return ONLY a JSON object — no markdown, no explanation, no code blocks.

{wrapped_review}"""


def build_prompt(wrapped_review: str) -> str:
    return _TEMPLATE.format(
        field_descriptions=_FIELD_DESCRIPTIONS,
        example=_EXAMPLE,
        wrapped_review=wrapped_review,
    )
