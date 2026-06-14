from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.core.reply.engine import _parse_reply, draft_reply
from app.core.reply.schema import ReplyRequest, ReplyTone
from app.core.schemas import ReviewExtraction


# ---------------------------------------------------------------------------
# _parse_reply — pure function, no async
# ---------------------------------------------------------------------------


def test_parse_reply_json_format() -> None:
    result = _parse_reply('{"reply_text": "Hello customer"}')
    assert result == "Hello customer"


def test_parse_reply_markdown_fence() -> None:
    raw = '```json\n{"reply_text": "Hello from markdown"}\n```'
    result = _parse_reply(raw)
    assert result == "Hello from markdown"


def test_parse_reply_missing_key() -> None:
    raw = '{"other": "x"}'
    result = _parse_reply(raw)
    assert result == '{"other": "x"}'


def test_parse_reply_raw_text() -> None:
    raw = "Thank you for your feedback."
    result = _parse_reply(raw)
    assert result == "Thank you for your feedback."


def test_parse_reply_strips_whitespace() -> None:
    raw = '{"reply_text": "  Hello  "}'
    result = _parse_reply(raw)
    assert result == "Hello"


# ---------------------------------------------------------------------------
# draft_reply — async, patching _call_groq at module level
# ---------------------------------------------------------------------------


def _make_extraction(cons: list[str], topics: list[str]) -> ReviewExtraction:
    """Construct a minimal ReviewExtraction for test use."""
    return ReviewExtraction.model_construct(
        product="test product",
        cons=cons,
        topics=topics,
        pros=[],
        competitor_mentions=[],
        feature_requests=[],
    )


async def test_draft_reply_basic() -> None:
    extraction = _make_extraction(cons=["slow delivery"], topics=["delivery"])
    request = ReplyRequest(
        text="Great product",
        tone=ReplyTone.professional,
        extraction=extraction,
    )

    # Reply must be ≥30 chars and mention "delivery" to pass guardrails cleanly.
    clean_reply = "Thank you for your feedback on the delivery experience!"
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(return_value=(f'{{"reply_text": "{clean_reply}"}}', 100, 50)),
    ):
        draft, tokens_in, tokens_out = await draft_reply(request)

    assert draft.reply_text == clean_reply
    assert draft.language == "en"
    # model_used is the actual model name from settings (groq_model_large default)
    assert draft.model_used == "llama-3.3-70b-versatile"
    assert draft.caveats == []
    assert "slow delivery" in draft.grounded_on
    assert "delivery" in draft.grounded_on
    assert tokens_in == 100
    assert tokens_out == 50


async def test_draft_reply_degrades_on_quota() -> None:
    extraction = _make_extraction(cons=["battery"], topics=["battery"])
    request = ReplyRequest(
        text="Battery is terrible",
        tone=ReplyTone.apologetic,
        extraction=extraction,
    )

    call_count = 0

    async def _side_effect(
        model: str,
        system_prompt: str,
        user_prompt: str,
        settings: object,
    ) -> tuple[str, int, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("rate_limit_exceeded")
        return ('{"reply_text": "Sorry to hear this."}', 80, 40)

    with patch("app.core.reply.engine._call_groq", new=_side_effect):
        draft, _tin, _tout = await draft_reply(request)

    assert draft.reply_text == "Sorry to hear this."
    assert any("degraded model" in c for c in draft.caveats)
    # model_used is the actual model name from settings (groq_model_small default)
    assert draft.model_used == "llama-3.1-8b-instant"


async def test_draft_reply_both_models_fail() -> None:
    extraction = _make_extraction(cons=["broken"], topics=["quality"])
    request = ReplyRequest(
        text="Product is broken",
        tone=ReplyTone.professional,
        extraction=extraction,
    )

    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(side_effect=RuntimeError("rate_limit_exceeded")),
    ):
        with pytest.raises(RuntimeError):
            await draft_reply(request)


async def test_draft_reply_uses_provided_extraction() -> None:
    extraction = _make_extraction(cons=["poor packaging"], topics=["packaging"])
    request = ReplyRequest(
        text="Packaging was damaged",
        tone=ReplyTone.apologetic,
        extraction=extraction,
    )

    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(
            return_value=(
                '{"reply_text": "We are sorry about the packaging issue."}',
                90,
                45,
            )
        ),
    ):
        # extract_with_llm is a lazy import in the else-branch; since extraction is
        # pre-provided, the else-branch is never entered, so we patch at its source
        # module as a belt-and-suspenders check that it is genuinely not called.
        with patch("app.core.llm.extract_with_llm", new=AsyncMock()) as mock_extract:
            draft, _tin, _tout = await draft_reply(request)
            mock_extract.assert_not_called()

    assert "poor packaging" in draft.grounded_on
    assert "packaging" in draft.grounded_on


async def test_draft_reply_signature_appended() -> None:
    extraction = _make_extraction(cons=[], topics=[])
    request = ReplyRequest(
        text="Good product overall",
        tone=ReplyTone.appreciative,
        extraction=extraction,
        signature="Best regards, Team",
    )

    # LLM reply does NOT include the signature
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(
            return_value=('{"reply_text": "Thank you for your kind review!"}', 60, 30)
        ),
    ):
        draft, _tin, _tout = await draft_reply(request)

    assert draft.reply_text.endswith("Best regards, Team")


async def test_draft_reply_signature_not_duplicated() -> None:
    extraction = _make_extraction(cons=[], topics=[])
    request = ReplyRequest(
        text="Good product overall",
        tone=ReplyTone.appreciative,
        extraction=extraction,
        signature="Best regards, Team",
    )

    # LLM reply ALREADY includes the signature
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(
            return_value=(
                '{"reply_text": "Thank you for your kind review!\\n\\nBest regards, Team"}',
                60,
                30,
            )
        ),
    ):
        draft, _tin, _tout = await draft_reply(request)

    assert draft.reply_text.count("Best regards, Team") == 1


async def test_draft_reply_guardrail_violation_becomes_caveat() -> None:
    extraction = _make_extraction(cons=["battery"], topics=["battery"])
    request = ReplyRequest(
        text="Battery drains very fast",
        tone=ReplyTone.apologetic,
        extraction=extraction,
    )

    # LLM returns a reply that triggers the fabrication guardrail
    fabricating_reply = "We will give you a full refund immediately."
    with patch(
        "app.core.reply.engine._call_groq",
        new=AsyncMock(
            return_value=(f'{{"reply_text": "{fabricating_reply}"}}', 100, 50)
        ),
    ):
        draft, _tin, _tout = await draft_reply(request)

    # Draft is returned (not raised)
    assert draft.reply_text != ""
    # At least one caveat contains "guardrail"
    assert any("guardrail" in c for c in draft.caveats)
