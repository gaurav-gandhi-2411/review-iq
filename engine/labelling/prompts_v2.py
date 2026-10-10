"""Judge prompt v2 (spec Amendment 3). Separate from prompts.py so pilot-1 stays reproducible.

One text call per item. No star rating is ever shown (mismatch is derived downstream). Aspects need a quote
that is a substring of the review; parse_text drops any aspect whose quote is not.
"""

from __future__ import annotations

import hashlib
import json
import re

from engine.labelling import schema as S

PROMPT_VERSION = "ri-judge-v2"

ASPECTS_V2: dict[str, tuple[str, ...]] = {
    "apparel": ("fit_size", "style", "fabric_quality", "comfort", "colour_accuracy"),
    "beauty": ("results_efficacy", "texture", "skin_reaction", "fragrance", "value"),
    "food": ("taste", "value", "ingredient_quality", "efficacy_claims", "quantity"),
    "vernacular": (),
}

# highest priority first; `praise` only when nothing actionable is present
PRIORITY = (
    "return_refund_request", "product_defect", "delivery", "customer_service", "question",
    "pricing", "competitor_comparison", "suggestion", "praise",
)  # fmt: skip

_INTENT_DEFS = """\
return_refund_request: asks for, or states they are doing, a return, refund or exchange
product_defect: the product itself is faulty, broke, leaks, fell apart, is not as described, or caused a reaction
delivery: shipping, courier, timing, parcel condition, missing items, rider behaviour (praise of fast shipping alone is praise)
customer_service: how the seller or support handled a contact (responsiveness, helpfulness, rudeness)
question: the reviewer asks something the seller could answer (rhetorical questions do not count)
pricing: price, value for money, price comparison
competitor_comparison: names or clearly refers to ANOTHER brand or product to compare (not just 'better than expected')
suggestion: a concrete wish or request for a change to the product, listing or service
praise: satisfaction with NO complaint, request or question of any kind
spam_irrelevant: no product content (ads, links, off-topic, gibberish, review of another product)"""

_URGENCY_DEFS = """\
high: physical harm or safety risk (pain, injury, skin reaction) even in a positive review; OR an explicit refund or return demand or legal threat; OR a systemic defect (arrived broken, same failure repeating)
medium: a concrete defect that stops normal use and is fixable, with no harm and no escalation (does not work, not as listed, wrong size that cannot be worn)
low: praise, a neutral remark, a wish, or a subjective dislike (colour not my taste, a little pricey) with no concrete defect"""


def text_prompt(text: str, category: str, retry: bool = False) -> str:
    aspects = ASPECTS_V2[category]
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
primary_intent: the ONE intent to act on. Rule: if the review contains ANY actionable complaint, request or question it is
  NOT praise. Choose the highest-priority class present, in this order: {list(PRIORITY)}; use spam_irrelevant only when
  there is no product content. One of {list(S.INTENTS)}
secondary_intents: a list (possibly empty) of OTHER intents from the same list that the review also clearly contains
sentiment: one of {list(S.SENTIMENTS)} (mixed = both clearly positive and clearly negative content)
urgency: one of {list(S.URGENCY)}
explicit_no_repurchase: "yes" only if the reviewer says they will NOT buy again or would NOT recommend it; otherwise "no"
{aspect_line}

Intent definitions:
{_INTENT_DEFS}

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


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def parse_text(raw: str, category: str, review_text: str) -> tuple[dict | None, int]:
    """-> (parsed or None, aspects dropped for lacking a verifiable quote). Strict on every enum."""
    d = _json(raw)
    if d is None:
        return None, 0
    try:
        primary, sent, urg, no_rep = (
            d["primary_intent"],
            d["sentiment"],
            d["urgency"],
            d["explicit_no_repurchase"],
        )
    except KeyError:
        return None, 0
    if (
        primary not in S.INTENTS
        or sent not in S.SENTIMENTS
        or urg not in S.URGENCY
        or no_rep not in S.YES_NO
    ):
        return None, 0
    sec = d.get("secondary_intents") or []
    if not isinstance(sec, list):
        return None, 0
    sec = sorted({x for x in sec if x in S.INTENTS and x != primary})
    raw_asp = d.get("aspects") or {}
    if not isinstance(raw_asp, dict):
        return None, 0
    body, kept, dropped = _norm(review_text), {}, 0
    for a, v in raw_asp.items():
        if a not in ASPECTS_V2[category]:
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
        "primary_intent": primary, "secondary_intents": sec, "sentiment": sent, "urgency": urg,
        "explicit_no_repurchase": no_rep, "aspects": kept,
    }, dropped  # fmt: skip
