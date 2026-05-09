"""Mocked unit tests for LLM client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.llm import _parse_response, extract_with_llm
from app.core.schemas import ReviewExtractionLLMOutput
from pydantic import ValidationError

_VALID_EXTRACTION = {
    "product": "Turbo-Vac 5000",
    "stars": None,
    "stars_inferred": 3,
    "pros": ["incredible suction", "very quiet"],
    "cons": ["poor battery life"],
    "buy_again": False,
    "sentiment": "mixed",
    "topics": ["suction", "battery"],
    "competitor_mentions": ["Dyson"],
    "urgency": "low",
    "feature_requests": [],
    "language": "en",
    "confidence": 0.85,
}

_VALID_JSON = json.dumps(_VALID_EXTRACTION)


def _make_groq_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestParseResponse:
    def test_valid_json(self) -> None:
        result = _parse_response(_VALID_JSON)
        assert result.product == "Turbo-Vac 5000"
        assert result.stars is None
        assert result.stars_inferred == 3

    def test_strips_markdown_fences(self) -> None:
        fenced = f"```json\n{_VALID_JSON}\n```"
        result = _parse_response(fenced)
        assert result.product == "Turbo-Vac 5000"

    def test_strips_generic_fences(self) -> None:
        fenced = f"```\n{_VALID_JSON}\n```"
        result = _parse_response(fenced)
        assert result.product == "Turbo-Vac 5000"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _parse_response("not json at all")

    def test_missing_required_field_raises(self) -> None:
        bad = {k: v for k, v in _VALID_EXTRACTION.items() if k != "product"}
        with pytest.raises(ValidationError):
            _parse_response(json.dumps(bad))


class TestExtractWithLLM:
    @pytest.fixture(autouse=True)
    def _mock_settings(self) -> None:
        with patch("app.core.llm.get_settings") as mock:
            settings = MagicMock()
            settings.groq_api_key = "gsk_test"
            settings.gemini_api_key = "AI_test"
            settings.groq_model = "llama-3.3-70b-versatile"
            settings.gemini_model = "gemini-1.5-flash"
            settings.llm_max_retries = 1
            settings.llm_timeout_seconds = 30
            mock.return_value = settings
            yield

    @pytest.mark.asyncio
    async def test_groq_success(self) -> None:
        mock_resp = _make_groq_response(_VALID_JSON)
        with patch("app.core.llm.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
            result, model, latency = await extract_with_llm("some prompt")

        assert result.product == "Turbo-Vac 5000"
        assert "llama" in model
        assert latency >= 0

    @pytest.mark.asyncio
    async def test_groq_returns_markdown_fenced_json(self) -> None:
        fenced = f"```json\n{_VALID_JSON}\n```"
        mock_resp = _make_groq_response(fenced)
        with patch("app.core.llm.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
            result, _, _ = await extract_with_llm("some prompt")

        assert result.product == "Turbo-Vac 5000"

    @pytest.mark.asyncio
    async def test_groq_retries_on_bad_json_then_succeeds(self) -> None:
        bad_resp = _make_groq_response("not json")
        good_resp = _make_groq_response(_VALID_JSON)
        with patch("app.core.llm.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(
                side_effect=[bad_resp, good_resp]
            )
            result, _, _ = await extract_with_llm("some prompt")

        assert result.product == "Turbo-Vac 5000"

    @pytest.mark.asyncio
    async def test_falls_back_to_gemini_on_groq_api_error(self) -> None:
        from groq import APIStatusError

        groq_err = APIStatusError(
            "rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
        gemini_result = ReviewExtractionLLMOutput(**_VALID_EXTRACTION)

        with patch("app.core.llm.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(side_effect=groq_err)
            with patch("app.core.llm._call_gemini", new=AsyncMock(return_value=gemini_result)):
                result, model, _ = await extract_with_llm("some prompt")

        assert result.product == "Turbo-Vac 5000"
        assert "gemini" in model

    @pytest.mark.asyncio
    async def test_raises_when_both_fail(self) -> None:
        from groq import APIStatusError

        groq_err = APIStatusError(
            "fail",
            response=MagicMock(status_code=500, headers={}),
            body={},
        )
        with (
            patch("app.core.llm.AsyncGroq") as MockGroq,
            patch(
                "app.core.llm._call_gemini", new=AsyncMock(side_effect=RuntimeError("gemini down"))
            ),
            pytest.raises(RuntimeError, match="Both LLM providers failed"),
        ):
            MockGroq.return_value.chat.completions.create = AsyncMock(side_effect=groq_err)
            await extract_with_llm("some prompt")

    @pytest.mark.asyncio
    async def test_model_hint_gemini_skips_groq(self) -> None:
        gemini_result = ReviewExtractionLLMOutput(**_VALID_EXTRACTION)
        with (
            patch("app.core.llm._call_gemini", new=AsyncMock(return_value=gemini_result)),
            patch("app.core.llm.AsyncGroq") as MockGroq,
        ):
            result, model, _ = await extract_with_llm("some prompt", model_hint="gemini")
            MockGroq.assert_not_called()

        assert "gemini" in model

    @pytest.mark.asyncio
    async def test_model_hint_groq_skips_gemini(self) -> None:
        mock_resp = _make_groq_response(_VALID_JSON)
        with patch("app.core.llm.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
            with patch("app.core.llm._call_gemini", new=AsyncMock()) as mock_gemini:
                result, model, _ = await extract_with_llm("some prompt", model_hint="groq")
                mock_gemini.assert_not_called()

        assert result.product == "Turbo-Vac 5000"
