"""A real, model-based prompt-injection pre-filter -- Session 13 P4.

Prior state (honest, but thin): app/core/sanitize.py's `detect_prompt_injection` is a
10-pattern regex list, covering exactly one phrasing family, in English only, with zero
purpose-built classifier behind it. This module adds `meta-llama/llama-prompt-guard-2-86m`
(Groq-hosted, free tier, its own SEPARATE 500K tokens/day budget -- confirmed live, does not
draw from the extraction models' 200K TPD pools, see P4c/ADR 0015) as a genuine second layer,
run BEFORE extraction, not after.

Measured behavior (this session, real live calls -- see eval/measure_prompt_guard_fpr.py and
the P4 injection suite for the full methodology): the model returns a single float in
`message.content` -- a probability-shaped score where confirmed benign text scores ~0.0003-0.01
and confirmed high-confidence attacks score ~0.99+. It reliably catches phrasing the regex
layer misses (e.g. "Disregard everything stated earlier..." -- no regex pattern matches this)
and non-English injection attempts (the regex layer is English-only). It does NOT catch every
attack family -- leetspeak/character-substitution evasion, role-confusion framing, and
field-targeted injection framing all scored LOW (0.0005-0.008, indistinguishable from benign)
in this session's own probing. This is disclosed here and in SECURITY.md as a real, measured
limit, not glossed over -- a two-layer defense with named gaps is a stronger, more honest claim
than an unqualified "we defend against prompt injection."

Fail-closed, never fail-open (P4a): if the classifier call itself fails for any reason (timeout,
network error, malformed response, non-numeric content), this returns SUSPICIOUS=True, not
None-treated-as-safe. The caller cannot distinguish "classifier said this is an attack" from
"classifier couldn't be reached" from the return value alone by design -- both must be treated
identically by every caller, which is exactly what "never fail open" requires.
"""

from __future__ import annotations

import structlog
from groq import AsyncGroq

log = structlog.get_logger(__name__)

INJECTION_GUARD_MODEL = "meta-llama/llama-prompt-guard-2-86m"

# Chosen from this session's real measured score distribution: confirmed-benign clusters at
# 0.0003-0.01, confirmed-high-confidence-attack clusters at 0.99+ -- a huge, clean gap with no
# observed borderline data between them (see eval/measure_prompt_guard_fpr.py's results).
# 0.5 sits in the middle of that gap; it is not a finely-tuned threshold because the data
# doesn't (yet) show a reason to tune it more precisely than "clearly separates the two
# clusters observed so far."
INJECTION_GUARD_THRESHOLD = 0.5


async def classify_injection_risk(text: str, *, api_key: str, timeout: float = 10.0) -> bool:
    """Return True if `text` should be treated as a likely prompt-injection attempt.

    Fails CLOSED (returns True -- suspicious) on any classifier error, per P4a: a
    pre-filter that silently lets traffic through when it can't be evaluated is not a
    pre-filter, it's a false sense of one.
    """
    try:
        client = AsyncGroq(api_key=api_key)
        response = await client.chat.completions.create(
            model=INJECTION_GUARD_MODEL,
            messages=[{"role": "user", "content": text}],
            timeout=timeout,
        )
        raw = response.choices[0].message.content
        if raw is None:
            log.error("injection_guard.empty_response")
            return True
        score = float(raw)
    except Exception:
        log.error("injection_guard.classifier_error", exc_info=True)
        return True

    if score >= INJECTION_GUARD_THRESHOLD:
        log.warning("injection_guard.flagged", score=score)
        return True
    return False
