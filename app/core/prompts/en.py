"""English extraction prompt — v2.2 (urgency rubric: defect→medium, harm-in-positive→high)."""

from __future__ import annotations

_FIELD_DESCRIPTIONS = """
Field definitions:
- product: The primary product name mentioned. Extract exactly as written.
- stars: ONLY if the reviewer explicitly states a numeric rating (e.g. "4/5", "3 stars", "★★★"). NULL otherwise. NEVER infer from sentiment.
- stars_inferred: Your holistic 1-5 estimate based on overall sentiment and content. Always populate.
- pros: ALL distinct positive attributes the reviewer mentions — extract every one. Each compliment, praise, or positive observation is a separate item, even if brief or phrased indirectly (e.g. "my cat appreciates the quiet" → "quiet operation"). Do NOT merge or drop any. Empty list only if the review contains zero positive statements.
- cons: ALL distinct negative attributes, complaints, or disappointments — extract every one. Each issue or criticism is a separate item, even if brief (e.g. "the handle feels flimsy" is a separate con from "battery dies fast"). Do NOT merge or drop any. Empty list only if the review contains zero negative statements.
- buy_again: true/false/null. Only false if reviewer explicitly says they would not repurchase. Null if ambiguous.
- sentiment: "positive" | "negative" | "neutral" | "mixed". Mixed = both positive and negative aspects.
- topics: ALL product topics discussed in this review. Include a topic for every pro and con you extracted — if you extracted a pro/con about noise, include "noise"; about build, include "build_quality". Use snake_case. Examples: battery, build_quality, noise, suction, price, customer_service, packaging, delivery, durability, design, performance.
- competitor_mentions: Other brand or product names explicitly mentioned. Empty list if none.
- urgency: "low" | "medium" | "high".
  HIGH = physical harm or safety risk (pain, aching, injury, bodily discomfort) — even in a high-rated or positive-tone review; OR explicit escalation (refund/return demand, legal threat); OR systemic defect (arrived broken, same failure repeating).
  MEDIUM = a concrete, fixable product defect with no harm and no escalation: bad microphone, poor fit, connectivity failure, battery underperforms, audio distortion, product doesn't match listing. Boundary: "Is there a specific fixable defect?" (yes → medium or higher). A reviewer reporting a broken feature without demanding a refund = medium.
  LOW = no concrete fixable defect: praise, neutral observation, or subjective preference only.
  CRITICAL: physical harm signals (pain, aching, discomfort, headache) → HIGH regardless of star rating or overall positive tone.
- feature_requests: Explicit suggestions or wishes for improvements. Empty list if none.
- language: always "en" for this prompt.
- confidence: Your confidence in the overall extraction quality, 0.0–1.0.
"""

_EXAMPLES = """
Example — no stars stated, fixable defects, no escalation (urgency=medium):
Review: "The suction is incredible and it runs whisper-quiet — my neighbour didn't even notice I was vacuuming. But the battery gives out after 20 minutes, and the handle creaks worryingly. For $250 I expected better."
Output: {"product": "...", "stars": null, "stars_inferred": 3, "pros": ["incredible suction", "whisper-quiet operation"], "cons": ["short battery life", "creaky handle", "poor value for price"], "buy_again": null, "sentiment": "mixed", "topics": ["suction", "noise", "battery", "build_quality", "price"], "competitor_mentions": [], "urgency": "medium", "feature_requests": [], "language": "en", "confidence": 0.9}

Example — explicit stars, all positive, no defects (urgency=low):
Review: "Love this! 5/5 stars. Perfect in every way."
Output: {"product": "...", "stars": 5, "stars_inferred": 5, "pros": ["overall satisfaction"], "cons": [], "buy_again": true, "sentiment": "positive", "topics": [], "competitor_mentions": [], "urgency": "low", "feature_requests": [], "language": "en", "confidence": 0.85}

Example — positive tone, high rating, but physical harm signal (urgency=high):
Review: "Sound quality is amazing and the Bluetooth pairs instantly — really happy with this purchase! Only thing is the ear cups press quite hard and my ears start aching after about 20 minutes. Still a great buy overall."
Output: {"product": "...", "stars": null, "stars_inferred": 4, "pros": ["amazing sound quality", "instant Bluetooth pairing"], "cons": ["ear cups cause ear aching after 20 minutes"], "buy_again": true, "sentiment": "positive", "topics": ["sound_quality", "bluetooth", "comfort"], "competitor_mentions": [], "urgency": "high", "feature_requests": [], "language": "en", "confidence": 0.9}
"""

_TEMPLATE = """\
Extract structured information from the customer review below.

{field_descriptions}

{examples}

Return ONLY a JSON object — no markdown, no explanation, no code blocks.

{wrapped_review}"""


def build_prompt(wrapped_review: str) -> str:
    return _TEMPLATE.format(
        field_descriptions=_FIELD_DESCRIPTIONS,
        examples=_EXAMPLES,
        wrapped_review=wrapped_review,
    )
