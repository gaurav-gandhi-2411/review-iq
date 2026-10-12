"""Fixed regular expressions that surface CANDIDATES for the rare review-intent classes (re-pilot 1).

Committed with spec Amendment 3 before the re-pilot runs. Candidates are not labels: the panel still decides.
Patterns are deliberately simple and English-only; Hinglish items are mined with the same patterns and will
mostly miss, which the re-pilot report states.
"""

from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern[str]] = {
    "delivery": re.compile(
        r"\b(?:deliver(?:y|ed)|courier|rider|never arrived|arrived (?:late|damaged|broken|crushed)|"
        r"took (?:\w+ ){0,2}(?:days|weeks)|shipping (?:was|took)|packag\w+ (?:was |were )?(?:damaged|torn|open))\b",
        re.I,
    ),
    "customer_service": re.compile(
        r"\b(?:customer (?:service|support|care)|support team|seller (?:did not|didn't|never)|"
        r"no (?:response|reply)|never (?:replied|responded)|rude|helpful staff)\b",
        re.I,
    ),
    "question": re.compile(
        r"(?:\b(?:does|is|are|can|could|will|do) (?:it|this|these|the)\b|\bhow (?:do|can|long|many)\b|"
        r"\bwhere (?:can|do)\b)[^.?!]{0,80}\?",
        re.I,
    ),
    "competitor_comparison": re.compile(
        r"\b(?:compared (?:to|with)|switched (?:from|to)|instead of (?:my |the )?(?:old|usual|previous|\w+ brand)|"
        r"better than (?:my |the |other )?(?:old|previous|usual|other|\w+ brand)|worse than (?:my |the )?(?:old|usual|previous)|"
        r"\bvs\.?\b|than (?:my )?(?:old|previous|usual))\b",
        re.I,
    ),
    "return_refund_request": re.compile(
        r"\b(?:refund|money back|send (?:it )?back|returning (?:it|this)|returned (?:it|this)|exchange|replacement)\b",
        re.I,
    ),
    "high_urgency": re.compile(
        r"\b(?:rash|burn(?:t|ed|s|ing)?|allerg\w+|sick|vomit\w*|pain\w*|injur\w+|hospital|swollen|swelling|"
        r"bleed\w*|infection|poison\w*|chok\w+|breakout|broke me out)\b",
        re.I,
    ),
}


def matches(text: str, cls: str) -> bool:
    return bool(PATTERNS[cls].search(text))
