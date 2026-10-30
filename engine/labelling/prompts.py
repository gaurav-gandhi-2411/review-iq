"""Versioned judge prompts and strict parsers. Bump PROMPT_VERSION on any wording change.

Two calls per review: the TEXT call never sees the star rating (so sentiment is not a copy of the stars);
the MISMATCH call sees the stars. The definitions mirror docs/specs/review-intent.md section 2.
"""

from __future__ import annotations

import hashlib
import json
import re

from engine.labelling import schema as S

PROMPT_VERSION = "ri-judge-v1"

_INTENT_DEFS = """\
product_defect: the product itself is faulty, broke, leaks, fell apart, is not as described, or caused a reaction
delivery: shipping, courier, timing, parcel condition, missing items, rider behaviour (praise of fast shipping alone is praise)
customer_service: how the seller or support handled a contact (responsiveness, helpfulness, rudeness)
pricing: price, value for money, price comparison
praise: satisfaction with no actionable complaint, no question and no request
question: the reviewer asks something the seller could answer (rhetorical questions do not count)
return_refund_request: asks for, or states they are doing, a return, refund or exchange
suggestion: a concrete wish or request for a change to the product, listing or service
competitor_comparison: names or clearly refers to ANOTHER brand or product to compare (not just 'better than expected')
spam_irrelevant: no product content (ads, links, off-topic, gibberish, review of another product)"""

_URGENCY_DEFS = """\
high: physical harm or safety risk (pain, injury, skin reaction) even in a positive review; OR explicit escalation (refund or return demand, legal threat); OR a systemic defect (arrived broken, same failure repeating)
medium: a concrete fixable defect with no harm and no escalation (does not work, not as listed, fit issue without pain)
low: praise, neutral observation or subjective preference only"""


def text_prompt(text: str, category: str) -> str:
    aspects = S.ASPECTS[category]
    aspect_line = (
        f"aspects: an object. Include an aspect ONLY if the review explicitly talks about it, and map it to "
        f"positive, negative or neutral. An aspect the review does not mention must be left OUT (never give it "
        f"neutral just because it is absent). If unsure, leave it out. Allowed aspects: {', '.join(aspects)}"
        if aspects
        else "aspects: always {}"
    )
    return f"""You label one product review for a seller. The review may be English, Hindi, or Hinglish (romanised Hindi).
Judge the text only.

Return ONE JSON object and nothing else, with exactly these keys:
primary_intent: the ONE thing the seller most needs to act on, one of {list(S.INTENTS)}
secondary_intents: a list (possibly empty) of OTHER intents from the same list that the review also clearly contains
sentiment: one of {list(S.SENTIMENTS)} (mixed = both clearly positive and clearly negative content)
urgency: one of {list(S.URGENCY)}
buy_again: yes only if the reviewer says they will repurchase or recommend; no only if they say they will not repurchase or would not recommend; otherwise unclear
{aspect_line}

Intent definitions:
{_INTENT_DEFS}

Urgency definitions:
{_URGENCY_DEFS}

Review:
\"\"\"{text}\"\"\"
JSON:"""


def mismatch_prompt(text: str, stars: float) -> str:
    return f"""A product review has a star rating of {stars:g} out of 5 and the text below.
Answer "yes" if the stars and the text point in OPPOSITE directions (for example 5 stars with a complaint about a defect,
or 1 star with clear praise). Answer "no" if they are consistent or the text is too short to tell.
Return ONE JSON object and nothing else: {{"mismatch": "yes" or "no"}}

Review:
\"\"\"{text}\"\"\"
JSON:"""


def prompt_hash() -> str:
    """Fingerprint of the exact prompt text, recorded next to every judge output."""
    parts = [
        PROMPT_VERSION,
        text_prompt("X", "beauty"),
        text_prompt("X", "vernacular"),
        mismatch_prompt("X", 3),
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def _json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def parse_text(raw: str, category: str) -> dict | None:
    """Strict: any invalid enum value makes the whole item None (counted as a judge failure, never guessed)."""
    d = _json(raw)
    if d is None:
        return None
    try:
        primary, sent, urg, buy = d["primary_intent"], d["sentiment"], d["urgency"], d["buy_again"]
    except KeyError:
        return None
    if (
        primary not in S.INTENTS
        or sent not in S.SENTIMENTS
        or urg not in S.URGENCY
        or buy not in S.BUY_AGAIN
    ):
        return None
    sec = d.get("secondary_intents") or []
    if not isinstance(sec, list):
        return None
    sec = sorted({x for x in sec if x in S.INTENTS and x != primary})
    aspects = d.get("aspects") or {}
    if not isinstance(aspects, dict):
        return None
    ok = {a: v for a, v in aspects.items() if a in S.ASPECTS[category] and v in S.ASPECT_SENTIMENTS}
    return {"primary_intent": primary, "secondary_intents": sec, "sentiment": sent, "urgency": urg,
            "buy_again": buy, "aspects": ok}  # fmt: skip


def parse_mismatch(raw: str) -> str | None:
    d = _json(raw)
    v = d.get("mismatch") if d else None
    return v if v in S.YES_NO else None
