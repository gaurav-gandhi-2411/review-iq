from __future__ import annotations

import re

from app.core.language import detect_language

_REPLY_MIN_CHARS = 30
_REPLY_MAX_CHARS = 2000

# Patterns that signal fabricated seller commitments in the reply text.
# These are backstop checks; the prompt is the primary prevention layer.
_FABRICATION_PATTERNS: list[re.Pattern[str]] = [
    # "we/i will [verb] [indirect-obj] [a] [full] [refund/replacement/discount/compensation/exchange]"
    # The optional (\w+\s+) captures indirect objects like "give *you* a full refund".
    re.compile(
        r"\b(we|i)\s+will\s+\w+\s+(\w+\s+)?(a\s+)?(full\s+)?"
        r"(refund|replacement|discount|compensation|exchange)\b",
        re.IGNORECASE,
    ),
    # Refund/replacement passively committed ("refund will be processed/issued/sent")
    re.compile(
        r"\b(refund|replacement)\s+(will\s+be\s+)?(processed|issued|sent|given)\b",
        re.IGNORECASE,
    ),
    # Explicit guarantee/promise
    re.compile(r"\bwe\s+(guarantee|promise)\b", re.IGNORECASE),
    re.compile(r"\bI\s+promise\b", re.IGNORECASE),
    # Specific timeline commitment
    re.compile(r"\bwithin\s+\d+\s+(business\s+)?(hour|day|week)s?\b", re.IGNORECASE),
    # Discount as a promise
    re.compile(r"\b\d+\s*%\s*off\b", re.IGNORECASE),
    # "no questions asked"
    re.compile(r"\bno\s+questions?\s+asked\b", re.IGNORECASE),
    # "free replacement/exchange"
    re.compile(r"\bfree\s+(replacement|exchange)\b", re.IGNORECASE),
]

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "with",
        "that",
        "this",
        "it",
        "its",
        "i",
        "we",
        "you",
        "he",
        "she",
        "they",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "not",
        "no",
        "by",
        "from",
        "as",
        "so",
        "if",
        "all",
        "which",
        "will",
        "can",
        "would",
        "could",
        "should",
    ]
)


def check_no_fabrication(reply_text: str) -> str | None:
    """Return a violation description if reply contains fabricated commitments, else None."""
    for pattern in _FABRICATION_PATTERNS:
        m = pattern.search(reply_text)
        if m:
            return f"fabricated commitment detected: {m.group()!r}"
    return None


def check_language_match(reply_text: str, expected_language: str) -> str | None:
    """Return violation description if reply language doesn't match expected, else None.

    hi and hi-en are treated as compatible (code-mixed Hinglish is close to Hindi).
    """
    detected = detect_language(reply_text)
    if expected_language == detected:
        return None
    if {expected_language, detected} == {"hi", "hi-en"}:
        return None
    return f"language mismatch: expected {expected_language!r}, reply detected as {detected!r}"


def check_length(reply_text: str) -> str | None:
    """Return violation description if reply is outside acceptable length bounds, else None."""
    n = len(reply_text)
    if n < _REPLY_MIN_CHARS:
        return f"reply too short ({n} chars, min {_REPLY_MIN_CHARS})"
    if n > _REPLY_MAX_CHARS:
        return f"reply too long ({n} chars, max {_REPLY_MAX_CHARS})"
    return None


def check_grounded(
    reply_text: str,
    cons: list[str],
    topics: list[str],
    language: str,
) -> str | None:
    """Return violation description if English reply appears ungrounded, else None.

    Only enforced for English — keyword matching is unreliable for transliterated Hindi/Hinglish.
    Passes automatically when there are no cons/topics to ground against.
    """
    if not cons and not topics:
        return None
    if language != "en":
        return None

    def _tokens(texts: list[str]) -> set[str]:
        result: set[str] = set()
        for t in texts:
            for word in re.findall(r"\b[a-z]{3,}\b", t.lower()):
                if word not in _STOPWORDS:
                    result.add(word)
        return result

    source_tokens = _tokens(cons + topics)
    if not source_tokens:
        return None

    reply_lower = reply_text.lower()
    if not any(tok in reply_lower for tok in source_tokens):
        return "reply appears ungrounded (no keywords from cons/topics found in reply)"
    return None


def run_guardrails(
    reply_text: str,
    *,
    expected_language: str,
    cons: list[str],
    topics: list[str],
) -> list[str]:
    """Run all guardrails and return a list of violation descriptions (empty = all passed)."""
    violations: list[str] = []
    for result in [
        check_no_fabrication(reply_text),
        check_language_match(reply_text, expected_language),
        check_length(reply_text),
        check_grounded(reply_text, cons, topics, expected_language),
    ]:
        if result is not None:
            violations.append(result)
    return violations
