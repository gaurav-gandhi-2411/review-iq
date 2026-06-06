"""Hinglish (Roman-script Hindi/English code-mix) extraction prompt — v2.1."""

from __future__ import annotations

_FIELD_DESCRIPTIONS = """
This review is written in Hinglish — a natural code-mix of Hindi (in Roman script) and English
common among Indian consumers. Words like "bahut", "hai", "nahi", "ekdum", "paisa vasool",
"mast", "yaar", "accha", "kharab" are Hindi.

IMPORTANT: Output ALL field values in English, regardless of input language.
- Translate/summarize Hindi words in pros, cons, topics, and feature_requests into plain English.
- For product: extract the product name exactly as it appears (often in English within Hinglish text).
- language: always "hi-en" for this prompt.
- Ignore "READ MORE" at the end of reviews — it is a marketplace UI artifact, not review content.

Field definitions:
- product: Extract the product name/category. Often English words within Hinglish text.
- stars: ONLY if explicitly stated as a number (e.g. "4 star diya", "★★★★"). NULL otherwise.
- stars_inferred: Your holistic 1-5 estimate of satisfaction. Always populate.
- pros: ALL positive points — translate into English short phrases.
- cons: ALL negative points — translate into English short phrases.
- buy_again: true if reviewer recommends; false if explicitly says won't buy or implies
  dissatisfaction; null if unclear.
- sentiment: "positive" | "negative" | "neutral" | "mixed".
- topics: English topic words. Use snake_case (e.g. sound_quality, battery, price, delivery_service).
- competitor_mentions: Brand names mentioned. Empty list if none.
- urgency: "high" (safety/return intent/anger), "medium" (frustration, delivery issues),
  "low" (normal feedback).
- feature_requests: Feature wishes — translate to English. Empty list if none.
- confidence: 0.0–1.0.

SARCASM AND BACKHANDED COMPLIMENTS:
- Parenthetical qualifiers in Hindi or English often NEGATE or DOWNGRADE the preceding statement.
  e.g. "Good sound (not much better than local earphone)" — the pro is weak, not genuinely good.
  e.g. "Nice connectivity (utna bhi nahi)" — "utna bhi nahi" means "not even that much"; negate it.
- When a reviewer uses explicit Pros/Cons headers, trust the structure but read parenthetical
  qualifiers carefully — they often reveal the true sentiment is lower than the label suggests.
- If pros are all backhanded, the overall sentiment is likely "mixed" or "negative" and
  stars_inferred should reflect dissatisfaction (1–3), not the positive labels.

SERVICE vs PRODUCT:
- Separate delivery/service complaints from product quality in topics and cons.
- A good product with bad delivery → sentiment "mixed", urgency "medium" (delivery frustration).
- Delivery topic keywords: delivery_service, delivery_speed, customer_service, packaging.

WARRANTY/RESOLUTION STORIES:
- When a reviewer describes a problem that was RESOLVED (warranty replacement, refund, fix),
  the final outcome drives buy_again and stars_inferred.
- Good resolution → buy_again=true, stars_inferred leaning toward 4.
- Slow or poor resolution → buy_again=null or false, stars_inferred 2–3.
"""

_EXAMPLES = """
Example 1 — Hinglish mixed review (standard case):
Review: <review>Superb earphone, sound ekdum mast hai yaar. Apple earphone ko competition dega. But battery bahut weak hai. Paisa vasool nahi laga for 2000 rupees.</review>
Output: {"product": "earphone", "stars": null, "stars_inferred": 3, "pros": ["excellent sound quality", "competitive with Apple"], "cons": ["poor battery life", "not value for money at 2000 rupees"], "buy_again": null, "sentiment": "mixed", "topics": ["sound_quality", "battery", "price", "comparison"], "competitor_mentions": ["Apple"], "urgency": "low", "feature_requests": [], "language": "hi-en", "confidence": 0.88}

Example 2 — Sarcastic backhanded pros (parenthetical negation):
Review: <review>Pros: Good sound quality(not much better than a cheap local earphone) Nice design(thoda plastic lagta hai)Cons: Gets uncomfortable after 30 min. Not worth the price.</review>
Output: {"product": "earphone", "stars": null, "stars_inferred": 2, "pros": ["acceptable sound quality", "decent design"], "cons": ["not significantly better than cheap alternatives", "feels plasticky", "uncomfortable after 30 minutes", "overpriced"], "buy_again": false, "sentiment": "negative", "topics": ["sound_quality", "comfort", "design", "price"], "competitor_mentions": [], "urgency": "low", "feature_requests": [], "language": "hi-en", "confidence": 0.85}

Example 3 — Short enthusiastic review with Hindi phrases:
Review: <review>Ekdum mast product hai bhai! Bahut accha sound, bass zabardast hai. Value for money, totally recommend karta hoon.</review>
Output: {"product": "general product", "stars": null, "stars_inferred": 5, "pros": ["excellent sound quality", "outstanding bass", "great value for money"], "cons": [], "buy_again": true, "sentiment": "positive", "topics": ["sound_quality", "bass", "value"], "competitor_mentions": [], "urgency": "low", "feature_requests": [], "language": "hi-en", "confidence": 0.92}

Example 4 — Product good, delivery bad (service vs product separation):
Review: <review>Product quality bahut achha hai, lekin delivery wala time pe nahi aaya. 3 din late tha. Delivery boy ghar pe nahi aata, khud lene jaana padta hai.</review>
Output: {"product": "general product", "stars": null, "stars_inferred": 3, "pros": ["good product quality"], "cons": ["3 days late delivery", "delivery boy doesn't come to door", "must go pick up yourself"], "buy_again": null, "sentiment": "mixed", "topics": ["product_quality", "delivery_service", "delivery_speed", "customer_service"], "competitor_mentions": [], "urgency": "medium", "feature_requests": [], "language": "hi-en", "confidence": 0.87}
"""

_TEMPLATE = """\
Extract structured information from the Hinglish customer review below.
Output all field values in English (translate from Hindi where needed).

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
