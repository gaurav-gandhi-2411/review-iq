"""Judge prompt v3 (spec Amendment 4): coarse taxonomy. Separate module so v1/v2 stay reproducible.

One text call per item, no star rating. Tasks: needs_action (yes/no), broad_intent (5 values), sentiment, urgency,
and aspect sentiment (beauty and food only) that needs a quote copied from the review.
"""

from __future__ import annotations

import hashlib

from engine.labelling import schema as S
from engine.labelling.prompts_v2 import _URGENCY_DEFS, _json, _norm

PROMPT_VERSION = "ri-judge-v3"

BROAD_INTENTS = ("product_issue", "delivery", "service", "praise", "other")
ASPECTS_V3: dict[str, tuple[str, ...]] = {
    "apparel": (),  # dropped in Amendment 4 (borderline in re-pilot 1)
    "beauty": ("results_efficacy", "texture", "skin_reaction", "fragrance", "value"),
    "food": ("taste", "value", "ingredient_quality", "efficacy_claims", "quantity"),
    "vernacular": (),
}

_NEEDS_ACTION = """\
needs_action = "yes" if the review contains at least ONE thing the seller can act on or owes a response to: a complaint about
  the product (defect, damage, not as described, adverse reaction, poor performance), the delivery (late, damaged parcel,
  missing item, courier) or the seller's service; an explicit return, refund or exchange request or action; or an
  unanswered question. "no" otherwise: praise, a neutral description, a subjective taste or fit opinion with no fault
  stated, a price remark with no request, a wish or suggestion with no complaint, a comparison, spam."""

_BROAD = """\
broad_intent = ONE value. If several apply choose the first that applies in this order:
  product_issue: the product is faulty, broke, damaged, not as described, caused a reaction, or underperforms
  delivery: shipping, courier, timing, parcel condition, missing items, rider behaviour (praise of fast shipping alone is not delivery)
  service: how the seller or support handled a contact, and return / refund / exchange handling or requests
  praise: satisfaction with NO complaint, request or question of any kind
  other: price remarks, suggestions, comparisons, questions with no complaint, neutral descriptions, spam or off-topic"""

_EXAMPLES = """\
Examples (review -> needs_action, broad_intent):
  "fell apart in the wash, returning" -> yes, product_issue
  "love it, but the pump broke on day two" -> yes, product_issue (a complaint outranks praise)
  "it is overpriced for a shirt" -> no, other
  "I wish it came in blue" -> no, other
  "does it contain nuts?" -> yes, other (a reply is owed)
  "great product, arrived fast" -> no, praise
  "parcel was crushed but the product is fine" -> yes, delivery
  "seller never replied to my message" -> yes, service"""


def text_prompt(text: str, category: str, retry: bool = False) -> str:
    aspects = ASPECTS_V3[category]
    aspect_line = (
        "aspects: an object. For each of these aspects that the review explicitly talks about, give "
        '{"sentiment": "positive"|"negative"|"neutral", "quote": "<the exact words copied from the review>"}. '
        "Leave an aspect OUT unless you can copy words from the review that talk about it. "
        f"Allowed aspects: {', '.join(aspects)}"
        if aspects
        else "aspects: always {}"
    )
    reminder = (
        "\nYour previous answer was not valid JSON with the required keys. Answer again with ONE valid JSON object only.\n"
        if retry
        else ""
    )
    return f"""You label one product review for a seller. The review may be English, Hindi, or Hinglish (romanised Hindi).
Judge the text only.{reminder}

Return ONE JSON object and nothing else, with exactly these keys:
needs_action: "yes" or "no"
broad_intent: one of {list(BROAD_INTENTS)}
sentiment: one of {list(S.SENTIMENTS)} (mixed = both clearly positive and clearly negative content)
urgency: one of {list(S.URGENCY)}
{aspect_line}

{_NEEDS_ACTION}

{_BROAD}

{_EXAMPLES}

Urgency definitions:
{_URGENCY_DEFS}

Review:
\"\"\"{text}\"\"\"
JSON:"""


def prompt_hash() -> str:
    parts = [
        PROMPT_VERSION,
        text_prompt("X", "beauty"),
        text_prompt("X", "vernacular"),
        text_prompt("X", "food", True),
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def parse_text(raw: str, category: str, review_text: str) -> tuple[dict | None, int]:
    """-> (parsed or None, aspects dropped for lacking a verifiable quote). Strict on every enum."""
    d = _json(raw)
    if d is None:
        return None, 0
    try:
        na, intent, sent, urg = d["needs_action"], d["broad_intent"], d["sentiment"], d["urgency"]
    except KeyError:
        return None, 0
    if (
        na not in S.YES_NO
        or intent not in BROAD_INTENTS
        or sent not in S.SENTIMENTS
        or urg not in S.URGENCY
    ):
        return None, 0
    raw_asp = d.get("aspects") or {}
    if not isinstance(raw_asp, dict):
        return None, 0
    body, kept, dropped = _norm(review_text), {}, 0
    for a, v in raw_asp.items():
        if a not in ASPECTS_V3[category]:
            continue
        if not isinstance(v, dict) or v.get("sentiment") not in S.ASPECT_SENTIMENTS:
            dropped += 1
            continue
        quote = _norm(str(v.get("quote", "")))
        if len(quote) < 3 or quote not in body:
            dropped += 1
            continue
        kept[a] = v["sentiment"]
    return {
        "needs_action": na, "broad_intent": intent, "sentiment": sent, "urgency": urg, "aspects": kept,
    }, dropped  # fmt: skip
