from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from groq import APIStatusError

from app.core.config import Settings, get_settings
from app.core.language import detect_language
from app.core.metrics import REPLY_DEGRADED_TOTAL, REPLY_DRAFT_TOTAL
from app.core.prompts.reply import build_reply_prompt
from app.core.providers.base import assert_privacy_safe
from app.core.providers.groq import GroqProvider
from app.core.reply.guardrails import run_guardrails
from app.core.reply.schema import ReplyDraft, ReplyRequest

log = structlog.get_logger(__name__)

# Languages where the small model produces incoherent composition (tested: 013/014
# "proud of ourselves for your trouble", "thank you for understanding it was defective").
# English degrades acceptably; vernacular does not.
_VERNACULAR_LANGUAGES = frozenset({"hi", "hi-en"})

_QUOTA_MESSAGE_SIGNALS = frozenset(["rate_limit_exceeded", "tokens per day", "tpd", "rate limit"])

# In cassette replay mode, a missing large-model cassette means the recording session
# degraded to the small model. Treat it as quota to replay the small-model cassette.
_CASSETTE_MISS_SIGNAL = "no cassette for key"


class VernacularModelUnavailableError(Exception):
    """Raised when the large model is quota-capped for a vernacular (hi/hi-en) reply.

    The small model produces incoherent vernacular composition — brand-damaging in
    customer-facing text. Callers should return 503 with Retry-After rather than
    silently posting a broken draft.
    """


def _is_quota_error(exc: Exception) -> bool:
    """Return True when exc represents a Groq quota/rate-limit/TPD cap,
    or a cassette miss in replay mode (recorded under the degraded model)."""
    for candidate in (exc, exc.__cause__):
        if candidate is None:
            continue
        if isinstance(candidate, APIStatusError) and candidate.status_code == 429:
            return True
        msg = str(candidate).lower()
        if any(signal in msg for signal in _QUOTA_MESSAGE_SIGNALS):
            return True
        if _CASSETTE_MISS_SIGNAL in msg:
            return True
    return False


def _parse_reply(raw: str) -> str:
    """Extract reply_text from LLM JSON output {"reply_text": "..."}.

    Falls back to returning raw text if JSON parsing fails or the key is absent.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner: list[str] = []
        for line in lines[1:]:
            if line.strip().startswith("```"):
                break
            inner.append(line)
        text = "\n".join(inner).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "reply_text" in parsed:
            return str(parsed["reply_text"]).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    return text


async def _call_groq(
    model: str,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
) -> tuple[str, int, int]:
    """Call a Groq model and return (raw_text, tokens_in, tokens_out)."""
    provider = GroqProvider(
        model=model,
        api_key=settings.groq_api_key,
        timeout=settings.llm_timeout_seconds,
    )
    assert_privacy_safe(provider, context="reply drafting")
    return await provider.complete(user_prompt, system_prompt=system_prompt)


async def draft_reply(
    request: ReplyRequest,
    *,
    settings: Settings | None = None,
) -> tuple[ReplyDraft, int, int]:
    """Draft a reply for the given review request.

    Returns (draft, total_tokens_in, total_tokens_out).
    - Uses large model first; degrades to small on quota error with a caveat flag.
    - Guardrail violations are added to caveats, never a hard block.
    - Raises RuntimeError only when both models are unavailable.
    """
    if settings is None:
        settings = get_settings()

    language = detect_language(request.text)
    total_tokens_in = 0
    total_tokens_out = 0
    caveats: list[str] = []

    # 1. Resolve cons/topics for grounding
    if request.extraction is not None:
        cons: list[str] = request.extraction.cons
        topics: list[str] = request.extraction.topics
    else:
        from app.core.llm import extract_with_llm
        from app.core.prompts import build_prompt
        from app.core.sanitize import sanitize, wrap_for_llm

        clean_text, _ = sanitize(request.text)
        wrapped = wrap_for_llm(clean_text)
        ext_prompt = build_prompt(wrapped, language)
        try:
            llm_output, _, _, ex_tin, ex_tout, _ = await extract_with_llm(
                ext_prompt, allow_gemini_fallback=False
            )
            cons = llm_output.cons
            topics = llm_output.topics
            total_tokens_in += ex_tin
            total_tokens_out += ex_tout
        except RuntimeError:
            log.warning("reply_engine.extraction_failed_grounding_skipped")
            cons = []
            topics = []
            caveats.append("reply not grounded in extraction (extraction unavailable)")

    # 2. Build the prompt
    system_prompt, user_prompt = build_reply_prompt(
        review_text=request.text,
        language=language,
        tone=request.tone,
        cons=cons,
        topics=topics,
        brand_name=request.brand_name,
        signature=request.signature,
    )

    # 3. Large model first; degrade to small on quota cap
    model_used: str
    reply_text: str
    try:
        raw, tin, tout = await _call_groq(
            settings.groq_model_large, system_prompt, user_prompt, settings
        )
        reply_text = _parse_reply(raw)
        model_used = settings.groq_model_large
        total_tokens_in += tin
        total_tokens_out += tout
    except (RuntimeError, APIStatusError) as exc:
        # APIStatusError is raised directly by GroqProvider.complete(); RuntimeError
        # is raised by router._call_provider which wraps it. Catch both so degradation
        # works on the direct-call path used here.
        if _is_quota_error(exc):
            REPLY_DEGRADED_TOTAL.inc()
            if language in _VERNACULAR_LANGUAGES:
                # Small model produces incoherent vernacular (tested: "proud of ourselves
                # for your trouble", "thank you for understanding it was defective").
                # Surface as 503 — caller should retry when large model is available.
                log.warning(
                    "reply_engine.vernacular_quota_hard_stop",
                    language=language,
                    model=settings.groq_model_large,
                )
                raise VernacularModelUnavailableError(
                    f"Large model quota reached; {language} reply drafting requires the "
                    "large model. Retry when quota resets (typically within minutes)."
                ) from exc
            # English degrades acceptably on the small model.
            caveats.append("drafted on reduced-capacity model — review carefully before posting")
            log.warning("reply_engine.degraded_to_small", model=settings.groq_model_small)
            raw, tin, tout = await _call_groq(
                settings.groq_model_small, system_prompt, user_prompt, settings
            )
            reply_text = _parse_reply(raw)
            model_used = settings.groq_model_small
            total_tokens_in += tin
            total_tokens_out += tout
        else:
            raise

    # 4. Guardrail check — violations become caveats, never a hard block
    violations = run_guardrails(
        reply_text,
        expected_language=language,
        cons=cons,
        topics=topics,
    )
    if violations:
        caveats.extend([f"guardrail: {v}" for v in violations])
        log.warning("reply_engine.guardrail_violations", violations=violations, model=model_used)

    # 5. Append signature if the LLM omitted it
    if request.signature and request.signature not in reply_text:
        reply_text = f"{reply_text}\n\n{request.signature}"

    REPLY_DRAFT_TOTAL.labels(language=language, tone=request.tone.value).inc()
    log.info(
        "reply_engine.drafted",
        language=language,
        tone=request.tone.value,
        model=model_used,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        caveats=caveats,
    )

    return (
        ReplyDraft(
            reply_text=reply_text,
            language=language,
            tone=request.tone,
            grounded_on=cons + topics,
            caveats=caveats,
            model_used=model_used,
            drafted_at=datetime.now(tz=UTC),
        ),
        total_tokens_in,
        total_tokens_out,
    )
