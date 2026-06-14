from __future__ import annotations

from app.core.reply.schema import ReplyTone

REPLY_PROMPT_VERSION = "v1.0"

_TONE_INSTRUCTIONS: dict[ReplyTone, str] = {
    ReplyTone.apologetic: (
        "Use an apologetic, empathetic tone — acknowledge the customer's experience with genuine "
        "regret and invite them to contact you so you can help resolve the matter."
    ),
    ReplyTone.appreciative: (
        "Use an appreciative, warm tone — thank the customer sincerely, acknowledge any concerns "
        "with care, and encourage further conversation."
    ),
    ReplyTone.professional: (
        "Use a professional, solution-oriented tone — acknowledge the concern clearly, express "
        "that you take it seriously, and invite the customer to reach out for resolution."
    ),
    ReplyTone.warm: (
        "Use a warm, friendly, conversational tone — make the customer feel heard and valued; "
        "address the concern with genuine care and kindness."
    ),
}

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hi-en": "Hinglish (a natural mix of Hindi and English as typically written by Indian speakers)",
    "other": "the same language as the customer review",
}

_CRITICAL_RULES = """\
CRITICAL RULES — you must follow all of these exactly:
1. NEVER promise a refund, replacement, discount, compensation, or any specific financial remedy. \
Those decisions belong to the human seller, not you.
2. NEVER commit to a specific timeline (e.g., "within 24 hours", "by tomorrow"). \
Use open-ended language such as "we will follow up with you soon."
3. NEVER make any statement that could be interpreted as an admission of legal liability.
4. NEVER make medical or safety claims.
5. The reply will be reviewed by a human before posting. Do not write "this is a draft" or \
any similar note — output only the reply text.
6. Write the reply IN {language_name}. The customer wrote in {language_name}; \
replying in the same language shows respect and makes the reply useful to them."""

_SYSTEM_TEMPLATE = """\
You are a professional customer-service reply writer{brand_line}.
You draft replies to customer reviews that are empathetic, helpful, and brand-appropriate.

{critical_rules}

{tone_instruction}

{concerns_section}\
{signature_section}\
Output ONLY a JSON object with this exact structure:
{{"reply_text": "the complete reply text goes here"}}

The reply_text value must be the complete, ready-to-post reply, written in {language_name}."""

_USER_TEMPLATE = """\
Customer review:
{review_text}

Draft a reply to this review. Follow all the rules in your instructions. \
Write in {language_name}. Address the specific concern(s) listed."""


def build_reply_prompt(
    review_text: str,
    language: str,
    tone: ReplyTone,
    cons: list[str],
    topics: list[str],
    brand_name: str | None,
    signature: str | None,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for the reply drafting LLM call.

    The LLM is instructed to output {"reply_text": "..."} (JSON mode compatible).
    """
    language_name = _LANGUAGE_NAMES.get(language, _LANGUAGE_NAMES["other"])
    brand_line = f" for {brand_name}" if brand_name else ""
    tone_instruction = f"Tone: {_TONE_INSTRUCTIONS[tone]}"
    critical_rules = _CRITICAL_RULES.format(language_name=language_name)

    concerns_parts: list[str] = []
    if cons:
        cons_str = "\n".join(f"  - {c}" for c in cons)
        concerns_parts.append(
            f"Specific complaints from this review (address all of them):\n{cons_str}"
        )
    if topics:
        topics_str = ", ".join(topics)
        concerns_parts.append(f"Topics discussed: {topics_str}")
    concerns_section = "\n".join(concerns_parts) + "\n\n" if concerns_parts else ""

    signature_section = (
        f'End the reply with this exact signature:\n"{signature}"\n\n'
        if signature
        else ""
    )

    system_prompt = _SYSTEM_TEMPLATE.format(
        brand_line=brand_line,
        critical_rules=critical_rules,
        tone_instruction=tone_instruction,
        concerns_section=concerns_section,
        signature_section=signature_section,
        language_name=language_name,
    )

    user_prompt = _USER_TEMPLATE.format(
        review_text=review_text,
        language_name=language_name,
    )

    return system_prompt, user_prompt
