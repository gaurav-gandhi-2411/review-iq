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
    # --- Hindi / Hinglish commitments and fault admission (caveat-level) ---
    # Romanised: "hum theek kar denge", "hum aapka product badal denge"
    re.compile(
        r"\bhum\s+(?:\w+\s+){0,3}(?:theek|thik|sahi|fix|badal|replace)\s+"
        r"(?:kar\s+)?(?:de(?:nge|ge)|karenge)\b",
        re.IGNORECASE,
    ),
    # "refund mil jayega", "paise wapas kar denge", "replacement bhej denge"
    re.compile(
        r"\b(?:refund|replacement|paisa|paise)\s+(?:\w+\s+){0,2}"
        r"(?:mil\s+ja(?:a)?(?:yega|ega)|ho\s+ja(?:a)?yega|bhej\s+de(?:nge|ge)|"
        r"(?:kar\s+)?de(?:nge|ge))\b",
        re.IGNORECASE,
    ),
    # Fault admission: "galti hamari thi", "hamari galti hai", "hamare end pe galti"
    re.compile(r"\bgalti\s+(?:hamari|humari|hamara|humara)\s+(?:thi|hai|rahi)\b", re.IGNORECASE),
    re.compile(r"\b(?:hamari|humari)\s+galti\b", re.IGNORECASE),
    re.compile(r"\b(?:hamare|humare)\s+end\s+(?:pe|par)\s+galti\b", re.IGNORECASE),
    # Timeline: "24 ghante mein", "2 din ke andar", "3 hafte me"
    re.compile(
        r"\b\d+\s+(?:din|dino|hafte|ghante|ghanta)\s+(?:ke\s+)?(?:andar|mein|me)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:pakka|guarantee)\s+(?:\w+\s+){0,2}(?:karenge|denge)\b", re.IGNORECASE),
    # Devanagari. No \b: combining vowel signs are not \w, which breaks word boundaries.
    re.compile(r"हम\s+(?:\S+\s+){0,3}(?:ठीक|सही)\s+(?:कर\s+)?(?:देंगे|करेंगे)"),
    re.compile(
        r"(?:रिफंड|रिप्लेसमेंट|पैसे|पैसा)\s+(?:\S+\s+){0,2}"
        r"(?:मिल\s+जाएगा|वापस\s+(?:कर\s+)?देंगे|भेज\s+देंगे|दे\s+देंगे)"
    ),
    re.compile(r"गलती\s+हमारी\s+(?:थी|है)|हमारी\s+गलती"),
    re.compile(r"\d+\s+(?:दिन|दिनों|घंटे|घंटों|हफ्ते|हफ्तों)\s+(?:के\s+)?(?:अंदर|में)"),
    re.compile(r"गारंटी|पक्का\s+(?:\S+\s+){0,2}(?:करेंगे|देंगे)"),
]

# --- Invented-detail detection -------------------------------------------------------------
# The drafter is given NO shop contact details, order data or amounts, so any contact detail,
# order/ticket number or money amount that is not in the review (or the shop's own signature)
# was made up by the model. Each kind maps to a bracketed placeholder the seller fills in.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"')\]]+|"
    r"\b[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.(?:com|in|net|org|co\.in)(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE,
)
# keyword-led identifiers: "order #12345", "ticket no. AB-4821", "tracking number 9988776655"
_ORDER_ID_RE = re.compile(
    r"\b(?:order|ticket|tracking|awb|invoice|case|reference|ref|complaint)"
    r"(?:\s+(?:no\.?|number|id|num))?\s*[:#\-]?\s*"
    r"(?=[A-Za-z0-9\-]*\d)([A-Za-z0-9][A-Za-z0-9\-]{3,})",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{6,}\d(?![\w])")
_DATE_RE = re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")
# currency-marked amounts only: bare numbers are too ambiguous ("2 weeks", "5 stars").
_MONEY_RE = re.compile(
    r"(?:₹|\bRs\.?|\bINR|\$)\s?\d[\d,]*(?:\.\d+)?|"
    r"\b\d[\d,]*(?:\.\d+)?\s?(?:rupees|rupaye|रुपये|रुपए)",
    re.IGNORECASE,
)

_PLACEHOLDERS = {
    "email": "[your support contact]",
    "url": "[your support link]",
    "phone": "[your support contact]",
    "order id": "[order number]",
    "amount": "[amount]",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def redact_invented_details(
    reply_text: str, source_texts: list[str]
) -> tuple[str, list[tuple[str, str]]]:
    """Replace emails/URLs/phones/order ids/money amounts absent from the sources.

    `source_texts` is everything the drafter legitimately knew: the (sanitized) review, its
    extracted cons/topics, and the seller-supplied signature/brand. A detail found in the
    sources is an echo and is kept; anything else is invented and is replaced by a bracketed
    placeholder. Returns (new_text, [(kind, removed_span), ...]).
    """
    sources_norm = _norm("\n".join(source_texts))
    sources_digits = _digits("\n".join(source_texts))
    sources_amounts = {
        d for t in source_texts for m in _MONEY_RE.finditer(t) if (d := _digits(m.group()))
    }
    found: list[tuple[str, str]] = []

    def _sub(kind: str, pattern: re.Pattern[str], text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            span = m.group()
            if kind == "phone":
                d = _digits(span)
                if not 8 <= len(d) <= 15 or _DATE_RE.match(span.strip()):
                    return span
                grounded = d in sources_digits
            elif kind == "amount":
                grounded = _digits(span) in sources_amounts
            elif kind == "order id":
                # judge the identifier only, not the leading keyword
                grounded = _norm(m.group(1)).rstrip(".,;:!?") in sources_norm
            else:
                grounded = _norm(span).rstrip(".,;:!?") in sources_norm
            if grounded:
                return span
            found.append((kind, span))
            return _PLACEHOLDERS[kind]

        return pattern.sub(repl, text)

    # Order matters: emails before URLs (the bare-domain pattern would eat an address's host),
    # keyword-led ids before phones (a long order number is not a phone number).
    text = _sub("email", _EMAIL_RE, reply_text)
    text = _sub("url", _URL_RE, text)
    text = _sub("order id", _ORDER_ID_RE, text)
    text = _sub("phone", _PHONE_RE, text)
    text = _sub("amount", _MONEY_RE, text)
    return text, found


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
