"""Input sanitization — PII redaction and prompt-injection guard.

PII redaction design (Wave 1 Section E): every PII span redacted from review text is
replaced with a UNIQUE, per-occurrence placeholder token (`[EMAIL_1]`, `[NAME_2]`, ...),
not a generic one -- so two distinct emails/names in one review don't collapse into an
ambiguous single placeholder. Each call to `redact_pii()`/`sanitize()` returns a
`RedactionMap` recording token -> original alongside the redacted text.

Reversibility: the map is held IN-MEMORY, per request, and never persisted -- there is no
token-map table. After the LLM returns its structured extraction, `rehydrate_output()`
walks the extraction's free-text fields and swaps any placeholder token that survived
back to its original value, before the extraction is stored/returned. This is what makes
redaction reversible without creating a new sensitive-data-at-rest surface: nothing is
permanently lost, the map just doesn't outlive the request that built it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

if TYPE_CHECKING:
    from app.core.schemas import ReviewExtraction

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# PII patterns (regex-based)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

_PHONE_RE = re.compile(
    r"""
    (?:
        \+?\d{1,3}[\s\-.]?          # optional country code
    )?
    (?:\(?\d{2,4}\)?[\s\-.]?)?     # optional area code
    \d{3,4}[\s\-.]?\d{3,4}         # main number
    (?:\s?(?:x|ext)\.?\s?\d{1,5})? # optional extension
    """,
    re.VERBOSE,
)

# Credit card-style 16-digit sequences
_CREDIT_CARD_RE = re.compile(r"\b(?:\d{4}[\s\-]?){3}\d{4}\b")

# "My name is <name>" or "I am <name>" patterns — fallback signal, see _redact_name_intro.
# The intro keywords are matched case-insensitively via the scoped (?i:...) group, but the
# capture group itself is deliberately NOT under a blanket re.IGNORECASE: a module-level
# IGNORECASE flag would fold [A-Z] into matching lowercase letters too, so a trailing
# lowercase word ("...Rajesh Kumar and I loved...") gets pulled into the captured name
# (fixed 2026-07-31 -- caught by the rehydration round-trip test).
_NAME_INTRO_RE = re.compile(
    r"\b(?i:my name is|i am|i'm|call me)\s+([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){0,2})",
)

# Prefixed order/invoice/tracking codes -- the prefix itself is the redaction signal, so
# these are always redacted regardless of surrounding context (ORD-123456, INV#98765,
# AWB1234567890, #123456789).
_ORDER_ID_PREFIXED_RE = re.compile(
    r"\b(?:ORD|INV|AWB|REF|TRK)[-#]?\d{4,}\b|#\d{5,}\b",
    re.IGNORECASE,
)

# Bare 6+ digit sequences with NO prefix are only order-ID candidates -- they are far more
# likely to be a price, quantity, or model number, so they require a contextual keyword
# nearby (see _redact_order_ids) before being redacted.
_BARE_DIGITS_RE = re.compile(r"\b\d{6,}\b")

_ORDER_CONTEXT_KEYWORDS_RE = re.compile(
    r"\b(order|invoice|tracking|track|awb|reference|shipment|shipped|package|parcel|delivery)\b",
    re.IGNORECASE,
)

# Word-window either side of a bare digit run to search for a contextual keyword. Mirrors
# the keyword-proximity technique scripts/check_no_hardcoded_metrics.py already uses for
# hand-typed-metric detection (line-window there, word-window here — same idea, applied to
# a single short review sentence instead of a document).
_ORDER_ID_CONTEXT_WINDOW = 4

# ---------------------------------------------------------------------------
# Prompt-injection patterns
# ---------------------------------------------------------------------------

_PI_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bignore\s+(all\s+)?(?:previous|above|prior)\s+instructions?\b",
        r"\bforget\s+(everything|all)\b",
        r"\bact\s+as\b.{0,40}\bAI\b",
        r"\byou\s+are\s+now\b",
        r"\bnew\s+instructions?\b",
        r"\bsystem\s*prompt\b",
        r"\bDAN\b",  # "Do Anything Now" jailbreak
        r"\bjailbreak\b",
        r"\bpretend\s+(you\s+are|to\s+be)\b",
        r"\boverride\s+(?:your\s+)?(?:instructions?|rules?|constraints?)\b",
    ]
]

# ---------------------------------------------------------------------------
# RedactionMap — ordered, per-request record of every redacted PII span
# ---------------------------------------------------------------------------

_TOKEN_LABELS: dict[str, str] = {
    "email": "EMAIL",
    "phone": "PHONE",
    "card": "CARD",
    "name": "NAME",
    "order_id": "ORDER_ID",
}


@dataclass(frozen=True)
class RedactionEntry:
    """One redacted PII span."""

    token: str
    original: str
    kind: str


@dataclass
class RedactionMap:
    """Per-call record of every PII span redacted by `redact_pii()`.

    In-memory only, never persisted — see module docstring. `add()` assigns the next
    per-kind counter so multiple distinct emails/names/etc. in one review get distinct
    tokens (`[EMAIL_1]`, `[EMAIL_2]`, ...) instead of collapsing into one ambiguous
    placeholder.
    """

    entries: list[RedactionEntry] = field(default_factory=list)
    _counters: dict[str, int] = field(default_factory=dict, repr=False)

    def add(self, kind: str, original: str) -> str:
        """Record a redacted span and return its unique placeholder token."""
        self._counters[kind] = self._counters.get(kind, 0) + 1
        label = _TOKEN_LABELS.get(kind, kind.upper())
        token = f"[{label}_{self._counters[kind]}]"
        self.entries.append(RedactionEntry(token=token, original=original, kind=kind))
        return token

    def as_dict(self) -> dict[str, str]:
        """Return {token: original} for rehydration."""
        return {e.token: e.original for e in self.entries}

    def __len__(self) -> int:
        return len(self.entries)


def _redact_regex(text: str, pattern: re.Pattern[str], kind: str, rmap: RedactionMap) -> str:
    """Replace every match of `pattern` with a unique per-occurrence token, recording it."""

    def _sub(m: re.Match[str]) -> str:
        return rmap.add(kind, m.group(0))

    return pattern.sub(_sub, text)


def _redact_order_ids(text: str, rmap: RedactionMap) -> str:
    """Redact order/invoice/tracking IDs.

    Two mechanisms:
    1. Prefixed alphanumeric codes (ORD-123456, INV#98765, AWB1234567890, #123456789) —
       always redacted; the prefix itself is the signal.
    2. A bare 6+ digit sequence with no prefix is only redacted when a contextual keyword
       (order/invoice/tracking/awb/reference/...) appears within a short word window on
       either side — a bare 6-digit number with no context is far more likely to be a
       price, quantity, or model number than an order ID, so it is left alone.
    """
    text = _redact_regex(text, _ORDER_ID_PREFIXED_RE, "order_id", rmap)

    matches = list(_BARE_DIGITS_RE.finditer(text))
    if not matches:
        return text

    replacements: list[tuple[int, int, str]] = []
    for m in matches:
        start, end = m.span()
        left_words = text[:start].split()[-_ORDER_ID_CONTEXT_WINDOW:]
        right_words = text[end:].split()[:_ORDER_ID_CONTEXT_WINDOW]
        window = " ".join(left_words + right_words)
        if _ORDER_CONTEXT_KEYWORDS_RE.search(window):
            token = rmap.add("order_id", m.group(0))
            replacements.append((start, end, token))

    # Replace back-to-front so earlier character offsets stay valid.
    for start, end, token in sorted(replacements, key=lambda r: r[0], reverse=True):
        text = text[:start] + token + text[end:]
    return text


@lru_cache(maxsize=1)
def _get_ner_pipeline() -> Any | None:
    """Load the spaCy small-English NER pipeline once per process (lazy singleton —
    mirrors app.core.language._get_lingua_detector's pattern for a slow-to-init model).

    KNOWN LIMITATION (documented, not silently overclaimed): en_core_web_sm is an
    English-language model. It does reasonably on English and Latin-script Hinglish
    person names ("Rajesh", "Priya", "Ankit") but does NOT reliably detect
    Devanagari-script Hindi names — no multilingual/Indic NER model is wired in here.
    Person-name redaction recall on pure-Hindi (Devanagari) review text is materially
    weaker than on English/Hinglish text until a multilingual NER model is evaluated.
    This is a stated scope boundary for Wave 1 Section E, not a customer-facing claim.

    Returns None (NER pass becomes a no-op; the `_redact_name_intro` regex fallback still
    runs) if the model can't be loaded — redaction must degrade gracefully, never crash
    the extraction pipeline over a missing/broken model.
    """
    try:
        import spacy

        return spacy.load("en_core_web_sm")
    except Exception as exc:  # noqa: BLE001 — must never crash extraction over NER load
        log.warning("sanitize.ner_model_unavailable", error=str(exc))
        return None


def _redact_names_ner(text: str, rmap: RedactionMap) -> str:
    """Redact PERSON-tagged spans using spaCy NER — the primary name-detection mechanism.

    See `_redact_name_intro` for the narrower self-introduction regex that runs after
    this as a fallback for whatever NER misses.
    """
    nlp = _get_ner_pipeline()
    if nlp is None:
        return text
    doc = nlp(text)
    person_spans = [(e.start_char, e.end_char, e.text) for e in doc.ents if e.label_ == "PERSON"]
    # Replace back-to-front so earlier character offsets stay valid.
    for start, end, original in sorted(person_spans, key=lambda s: s[0], reverse=True):
        token = rmap.add("name", original)
        text = text[:start] + token + text[end:]
    return text


def _redact_name_intro(text: str, rmap: RedactionMap) -> str:
    """Fallback for self-introductions ("my name is X") not already caught by NER —
    belt-and-suspenders: a self-intro should always be redacted even if NER misses it."""

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        prefix = m.group(0)[: m.start(1) - m.start(0)]
        token = rmap.add("name", name)
        return prefix + token

    return _NAME_INTRO_RE.sub(_sub, text)


def _pii_redaction_enabled() -> bool:
    """Eval-only escape hatch for measuring the extraction-accuracy delta of PII
    redaction (Wave 1 Section E critical gate). Deliberately NOT read from
    app.core.config.Settings and never exposed via any public API or request
    parameter — production default is always True (redaction on). Set
    REVIEW_IQ_DISABLE_PII_REDACTION_FOR_EVAL=1 only when running the redaction-off arm
    of scripts/measure_redaction_accuracy_delta.py.
    """
    return os.environ.get("REVIEW_IQ_DISABLE_PII_REDACTION_FOR_EVAL", "").strip() != "1"


def redact_pii(text: str) -> tuple[str, RedactionMap]:
    """Remove PII from review text before sending it to an LLM.

    Order matters: email/card/order-ID/phone (regexes, least-to-most digit-ambiguous)
    run first, so name detection never misfires on a digit run or an email local-part.
    NER runs before the name-intro regex so the regex only catches what NER missed.

    Args:
        text: Raw review text.

    Returns:
        Tuple of (redacted text, RedactionMap of every span redacted).
    """
    rmap = RedactionMap()

    text = _redact_regex(text, _EMAIL_RE, "email", rmap)
    text = _redact_regex(text, _CREDIT_CARD_RE, "card", rmap)  # before phone/order-id digits
    text = _redact_order_ids(text, rmap)
    text = _redact_regex(text, _PHONE_RE, "phone", rmap)
    text = _redact_names_ner(text, rmap)
    text = _redact_name_intro(text, rmap)

    if rmap.entries:
        log.info(
            "sanitize.pii_redacted",
            count=len(rmap.entries),
            kinds=sorted({e.kind for e in rmap.entries}),
        )

    return text, rmap


def redact_injections(text: str) -> str:
    """Replace matched prompt-injection phrases with a neutralising marker.

    Breaks the command portion of injection attempts while leaving genuine
    review content intact for extraction.
    """
    for pattern in _PI_PATTERNS:
        text = pattern.sub("[INJECTION_REMOVED]", text)
    return text


def detect_prompt_injection(text: str) -> bool:
    """Return True if the text looks like a prompt-injection attempt.

    Does NOT modify the text — caller decides whether to reject or log.
    """
    for pattern in _PI_PATTERNS:
        if pattern.search(text):
            log.warning("sanitize.pi_detected", pattern=pattern.pattern[:60])
            return True
    return False


def sanitize(text: str, max_length: int = 5000) -> tuple[str, bool, RedactionMap]:
    """Full sanitization pipeline.

    1. Truncate to max_length.
    2. Redact PII (unless disabled for the eval accuracy-delta measurement, see
       `_pii_redaction_enabled`).
    3. Detect prompt injection (does not strip — logged and flagged).

    Args:
        text: Raw review text from the caller.
        max_length: Hard cap on characters.

    Returns:
        Tuple of (sanitized text, is_suspicious, RedactionMap — empty when redaction
        was skipped or nothing matched). Callers that build LLM-produced structured
        output should pass the RedactionMap to `rehydrate_output`/`rehydrate_text`
        before persisting or returning that output.
    """
    if len(text) > max_length:
        text = text[:max_length]
        log.info("sanitize.truncated", max_length=max_length)

    if _pii_redaction_enabled():
        text, rmap = redact_pii(text)
    else:
        rmap = RedactionMap()

    is_suspicious = detect_prompt_injection(text)
    if is_suspicious:
        text = redact_injections(text)
    return text, is_suspicious, rmap


def wrap_for_llm(text: str) -> str:
    """Wrap sanitized review text in delimiters for safe LLM injection.

    The system prompt tells the model to treat content inside <review> as
    user data only, never as instructions.
    """
    return f"<review>\n{text}\n</review>"


def rehydrate_text(text: str, redaction_map: RedactionMap) -> str:
    """Replace any redaction placeholder tokens present in `text` with the original
    PII values they stood in for. No-op when the map is empty or none of its tokens
    appear in `text`."""
    for token, original in redaction_map.as_dict().items():
        if token in text:
            text = text.replace(token, original)
    return text


ExtractionT = TypeVar("ExtractionT", bound="ReviewExtraction")

# Free-text fields on ReviewExtraction (and ReviewExtractionV2) that an LLM's structured
# output could echo a redaction placeholder token back into. Deliberately excludes
# enum/scalar fields (sentiment, urgency, language, stars, ...) and extraction_meta,
# which never carry review prose.
_REHYDRATE_LIST_FIELDS: tuple[str, ...] = (
    "pros",
    "cons",
    "feature_requests",
    "topics",
    "competitor_mentions",
)


def rehydrate_output(extraction: ExtractionT, redaction_map: RedactionMap) -> ExtractionT:
    """Restore original PII values into an LLM-produced ReviewExtraction (or
    ReviewExtractionV2) that may echo a redaction placeholder token back in its
    free-text fields.

    Call this immediately after `extract_with_llm()` returns and before the extraction
    is persisted or returned to the caller — see module docstring for why this closes
    the reversibility gap between the redacted LLM-bound text and the original review
    text already stored unredacted in the same DB row.
    """
    if not redaction_map.entries:
        return extraction

    updates: dict[str, Any] = {"product": rehydrate_text(extraction.product, redaction_map)}
    for field_name in _REHYDRATE_LIST_FIELDS:
        updates[field_name] = [
            rehydrate_text(item, redaction_map) for item in getattr(extraction, field_name)
        ]
    return extraction.model_copy(update=updates)
