"""Input sanitization — PII redaction and prompt-injection guard."""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# PII patterns (regex-based, Phase 1)
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

# "My name is <name>" or "I am <name>" patterns
_NAME_INTRO_RE = re.compile(
    r"\b(?:my name is|i am|i'm|call me)\s+([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){0,2})",
    re.IGNORECASE,
)

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


def redact_pii(text: str) -> tuple[str, int]:
    """Remove PII from review text before sending to LLM.

    Args:
        text: Raw review text.

    Returns:
        Tuple of (redacted text, count of PII spans removed).
    """
    count = 0

    def _replace(pattern: re.Pattern[str], replacement: str, t: str) -> tuple[str, int]:
        matches = pattern.findall(t)
        return pattern.sub(replacement, t), len(matches)

    text, n = _replace(_EMAIL_RE, "[EMAIL]", text)
    count += n
    text, n = _replace(
        _CREDIT_CARD_RE, "[CARD]", text
    )  # before phone — cards are 16-digit sequences
    count += n
    text, n = _replace(_PHONE_RE, "[PHONE]", text)
    count += n

    # Name intros — replace the whole phrase
    def _redact_name(m: re.Match[str]) -> str:
        prefix = m.group(0)[: m.start(1) - m.start(0)]
        return prefix + "[NAME]"

    new_text, n_subs = re.subn(_NAME_INTRO_RE, _redact_name, text)
    text = new_text
    count += n_subs

    if count:
        log.info("sanitize.pii_redacted", count=count)

    return text, count


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


def sanitize(text: str, max_length: int = 5000) -> tuple[str, bool]:
    """Full sanitization pipeline.

    1. Truncate to max_length.
    2. Redact PII.
    3. Detect prompt injection (does not strip — logged and flagged).

    Args:
        text: Raw review text from the caller.
        max_length: Hard cap on characters.

    Returns:
        Tuple of (sanitized text, is_suspicious).
    """
    if len(text) > max_length:
        text = text[:max_length]
        log.info("sanitize.truncated", max_length=max_length)

    text, _ = redact_pii(text)
    is_suspicious = detect_prompt_injection(text)
    if is_suspicious:
        text = redact_injections(text)
    return text, is_suspicious


def wrap_for_llm(text: str) -> str:
    """Wrap sanitized review text in delimiters for safe LLM injection.

    The system prompt tells the model to treat content inside <review> as
    user data only, never as instructions.
    """
    return f"<review>\n{text}\n</review>"
