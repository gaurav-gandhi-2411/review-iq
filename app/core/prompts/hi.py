"""Hindi (Devanagari script) extraction prompt — v2.0."""

from __future__ import annotations

_FIELD_DESCRIPTIONS = """
This review is written in Hindi using Devanagari script. Brand/product names may appear in English.

IMPORTANT: Output ALL field values in English, even though the input is in Hindi.
- Translate Hindi content into English for pros, cons, topics, and feature_requests.
- For product: use the English brand/product name if present; otherwise translate to English category.
- language: always "hi" for this prompt.

Field definitions:
- product: The product name or category in English (translate if needed).
- stars: ONLY if an explicit numeric rating is in the text (e.g. "4 स्टार"). NULL otherwise.
- stars_inferred: Your holistic 1-5 satisfaction estimate. Always populate.
- pros: ALL positive points — translate to English short phrases.
- cons: ALL negative points — translate to English short phrases.
- buy_again: true if reviewer recommends; false if explicitly won't buy; null if unclear.
- sentiment: "positive" | "negative" | "neutral" | "mixed".
- topics: English topic words. Use snake_case (e.g. sound_quality, battery, price, safety).
- competitor_mentions: Brand names mentioned. Empty list if none.
- urgency: "high" (safety issue, return intent, danger), "medium" (frustration), "low" (normal feedback).
- feature_requests: Feature wishes in English. Empty list if none.
- confidence: 0.0–1.0.
"""

_EXAMPLE = """
Example — Hindi Devanagari review:
Review: <review>Boat के ईयरफोन बहुत बढ़िया हैं! साउंड क्वालिटी कमाल की है। सबको recommend करूंगा। पैसा वसूल प्रोडक्ट है।</review>
Output: {"product": "Boat earphone", "stars": null, "stars_inferred": 5, "pros": ["excellent sound quality", "value for money"], "cons": [], "buy_again": true, "sentiment": "positive", "topics": ["sound_quality", "value"], "competitor_mentions": [], "urgency": "low", "feature_requests": [], "language": "hi", "confidence": 0.92}

Example — Hindi safety complaint:
Review: <review>बहुत खतरनाक प्रोडक्ट! Amazon se liya adapter से करंट लग गया। तुरंत रिफंड चाहिए।</review>
Output: {"product": "adapter", "stars": null, "stars_inferred": 1, "pros": [], "cons": ["electric shock risk", "dangerous product"], "buy_again": false, "sentiment": "negative", "topics": ["safety", "electric_shock"], "competitor_mentions": [], "urgency": "high", "feature_requests": [], "language": "hi", "confidence": 0.95}
"""

_TEMPLATE = """\
Extract structured information from the Hindi customer review below.
The review is in Devanagari script. Output ALL field values in English (translate from Hindi).

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
