from __future__ import annotations

from app.core.authenticity.schema import AuthenticityFlag

# Phrases that strongly indicate an incentivized/non-organic review.
# Covers English and common Hinglish variants.
_INCENTIVIZED_PHRASES: list[str] = [
    # English
    "received free",
    "free sample",
    "free product",
    "in exchange for",
    "discount code",
    "gifted",
    "sponsored",
    "paid review",
    "complimentary",
    "in return for review",
    "provided for review",
    "received for testing",
    # Hinglish
    "muft mila",
    "free mila",
    "sample mila",
    "discount mila",
    "sponsered",
    "gift mila",
]

_POSITIVE_WORDS: frozenset[str] = frozenset(
    [
        "great",
        "love",
        "amazing",
        "excellent",
        "best",
        "perfect",
        "good",
        "awesome",
        "fantastic",
        "outstanding",
        "superb",
        "brilliant",
    ]
)

_NEGATIVE_WORDS: frozenset[str] = frozenset(
    [
        "bad",
        "poor",
        "terrible",
        "horrible",
        "worst",
        "awful",
        "disappointing",
        "useless",
        "broken",
        "defective",
        "waste",
        "refund",
    ]
)


def score_incentivized_phrases(text: str) -> tuple[float, bool]:
    """Return (penalty, flagged).

    penalty=0.8 if any incentivized phrase is found (case-insensitive), else 0.0.
    """
    lowered = text.lower()
    for phrase in _INCENTIVIZED_PHRASES:
        if phrase in lowered:
            return 0.8, True
    return 0.0, False


def score_brevity(text: str) -> tuple[float, bool]:
    """Return (penalty, flagged).

    penalty=0.6 if word count < 8, else 0.0.
    """
    if len(text.split()) < 8:
        return 0.6, True
    return 0.0, False


def score_repetition(text: str) -> tuple[float, bool]:
    """Return (penalty, flagged) based on word-level repetition.

    repetition_ratio = repeated_word_count / total_words.
    penalty = min(1.0, ratio * 2.0). flagged if ratio > 0.30.
    """
    words = text.lower().split()
    total = len(words)
    if total == 0:
        return 0.0, False

    from collections import Counter

    counts = Counter(words)
    # Words that appear more than once contribute their excess occurrences
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    ratio = repeated / total
    penalty = min(1.0, ratio * 2.0)
    flagged = ratio > 0.30
    return penalty, flagged


def score_rating_text_mismatch(text: str, stars: int | None) -> tuple[float, bool]:
    """Return (penalty, flagged) for star-rating vs sentiment disagreement.

    - stars >= 4 but negative words dominate → mismatch, penalty=0.7
    - stars <= 2 but positive words dominate → mismatch, penalty=0.7
    - Otherwise penalty=0.0, flagged=False.
    """
    if stars is None:
        return 0.0, False

    words = text.lower().split()
    pos_count = sum(1 for w in words if w.strip(".,!?") in _POSITIVE_WORDS)
    neg_count = sum(1 for w in words if w.strip(".,!?") in _NEGATIVE_WORDS)

    if stars >= 4 and neg_count > pos_count:
        return 0.7, True
    if stars <= 2 and pos_count > neg_count:
        return 0.7, True
    return 0.0, False


def compute_heuristic_score(
    text: str,
    stars: int | None = None,
) -> tuple[float, list[AuthenticityFlag]]:
    """Aggregate heuristic signals into a single genuineness score in [0, 1].

    Weighted penalty accumulation:
        incentivized_phrase : weight=1.0
        brevity             : weight=0.5
        repetition          : weight=0.4
        rating_mismatch     : weight=0.8

    score = 1.0 - clamp(total_penalty, 0.0, 1.0)
    """
    total_penalty = 0.0
    flags: list[AuthenticityFlag] = []

    incent_penalty, incent_flagged = score_incentivized_phrases(text)
    if incent_flagged:
        total_penalty += incent_penalty * 1.0
        flags.append(AuthenticityFlag.INCENTIVIZED_PHRASE)

    brev_penalty, brev_flagged = score_brevity(text)
    if brev_flagged:
        total_penalty += brev_penalty * 0.5
        flags.append(AuthenticityFlag.EXCESSIVE_BREVITY)

    rep_penalty, rep_flagged = score_repetition(text)
    if rep_flagged:
        total_penalty += rep_penalty * 0.4
        flags.append(AuthenticityFlag.REPETITIVE_CONTENT)

    mismatch_penalty, mismatch_flagged = score_rating_text_mismatch(text, stars)
    if mismatch_flagged:
        total_penalty += mismatch_penalty * 0.8
        flags.append(AuthenticityFlag.RATING_TEXT_MISMATCH)

    total_penalty = max(0.0, min(1.0, total_penalty))
    score = 1.0 - total_penalty
    return score, flags
