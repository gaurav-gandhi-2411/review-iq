"""429 visibility: parsing of Groq rate-limit bodies and the llm.rate_limited / llm.failover events.

No network: the Groq client and the secondary are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import app.core.llm as llm_module
import pytest
from app.core.config import Settings
from app.core.rate_limit_events import (
    log_rate_limited,
    numeric_ratelimit_headers,
    parse_rate_limit_message,
    parse_retry_seconds,
)
from app.core.schemas import ReviewExtractionLLMOutput
from groq import APIStatusError
from structlog.testing import capture_logs

TPD_MSG = (
    "Rate limit reached for model `openai/gpt-oss-20b` in organization `org_01abcXYZ9` "
    "service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199445, "
    "Requested 972. Please try again in 3m0.144s."
)
TPM_MSG = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01abcXYZ9` "
    "service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 7500, "
    "Requested 900. Please try again in 4.2s."
)

_GOOD = ReviewExtractionLLMOutput(
    sentiment="positive",
    stars=None,
    buy_again=True,
    pros=["good"],
    cons=[],
    topics=["quality"],
    language="en",
    confidence=0.9,
)
_GOOD_RAW = json.dumps(_GOOD.model_dump())


def _events(logs: list[dict], name: str) -> list[dict]:
    return [e for e in logs if e["event"] == name]


def _err_429(msg: str = TPD_MSG, headers: dict | None = None) -> APIStatusError:
    return APIStatusError(
        message=msg,
        response=MagicMock(status_code=429, headers=headers or {}),
        body={"error": {"message": msg, "type": "tokens", "code": "rate_limit_exceeded"}},
    )


class TestParsing:
    def test_verbatim_tpd_body(self) -> None:
        f = parse_rate_limit_message(TPD_MSG)
        assert f["limit_type"] == "TPD"
        assert f["org_id"] == "org_01abcXYZ9"
        assert (f["limit"], f["used"], f["requested"]) == (200000, 199445, 972)
        assert f["retry_after_s"] == pytest.approx(180.144)

    def test_tpm_body(self) -> None:
        f = parse_rate_limit_message(TPM_MSG)
        assert f["limit_type"] == "TPM"
        assert (f["limit"], f["used"], f["requested"]) == (8000, 7500, 900)
        assert f["retry_after_s"] == pytest.approx(4.2)

    def test_body_without_numbers(self) -> None:
        f = parse_rate_limit_message("Too many requests")
        assert f == {
            "limit_type": None,
            "org_id": None,
            "limit": None,
            "used": None,
            "requested": None,
            "retry_after_s": None,
        }

    def test_limit_type_from_words(self) -> None:
        assert parse_rate_limit_message("hit requests per day")["limit_type"] == "RPD"

    @pytest.mark.parametrize(
        ("text", "secs"),
        [("try again in 1h2m3s", 3723.0), ("try again in 250ms", 0.25), ("nothing", None)],
    )
    def test_retry_durations(self, text: str, secs: float | None) -> None:
        assert parse_retry_seconds(text) == secs

    def test_numeric_headers_only(self) -> None:
        h = {
            "x-ratelimit-remaining-tokens": "12",
            "x-ratelimit-reset-tokens": "2m59.56s",
            "retry-after": "180",
            "content-type": "application/json",
        }
        assert numeric_ratelimit_headers(h) == {
            "x-ratelimit-remaining-tokens": 12.0,
            "retry-after": 180.0,
        }

    def test_headers_unreadable_do_not_raise(self) -> None:
        assert numeric_ratelimit_headers(None) == {}


class TestLogRateLimited:
    @pytest.mark.parametrize("body", ["{not json", None, b"\xff\xfe", {"error": 5}, 42])
    def test_malformed_bodies_never_raise(self, body: object) -> None:
        with capture_logs() as logs:
            log_rate_limited(
                provider="groq", model="m", tier=None, role="primary", body=body, headers=None
            )
        ev = _events(logs, "llm.rate_limited")
        assert len(ev) == 1
        assert ev[0]["limit_type"] is None

    def test_json_string_body_and_warning_level(self) -> None:
        body = json.dumps({"error": {"message": TPD_MSG}})
        with capture_logs() as logs:
            log_rate_limited(
                provider="groq",
                model="openai/gpt-oss-20b",
                tier="small",
                role="primary",
                body=body,
                headers={"retry-after": "180"},
            )
        ev = _events(logs, "llm.rate_limited")[0]
        assert ev["log_level"] == "warning"
        assert ev["limit_type"] == "TPD"
        assert ev["used"] == 199445
        assert ev["tier"] == "small"
        assert ev["ratelimit_headers"] == {"retry-after": 180.0}

    def test_no_secrets_or_prompt_fields(self) -> None:
        with capture_logs() as logs:
            log_rate_limited(
                provider="groq", model="m", tier="small", role="primary", body={}, headers={}
            )
        keys = set(_events(logs, "llm.rate_limited")[0])
        assert not keys & {"api_key", "prompt", "user_prompt", "text", "authorization"}


def _settings(**kw: object) -> Settings:
    base = dict(
        GROQ_API_KEY="fake",
        SECONDARY_PROVIDER_API_KEY="sec-key",
        SECONDARY_PROVIDER_MODEL="sec-model",
        SECONDARY_PROVIDER_KIND="groq",
        LLM_MAX_RETRIES=0,
        LLM_TIMEOUT_SECONDS=30,
    )
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def _ok_response() -> AsyncMock:
    r = AsyncMock()
    r.choices = [AsyncMock()]
    r.choices[0].message.content = _GOOD_RAW
    r.usage.prompt_tokens = 10
    r.usage.completion_tokens = 5
    return r


@pytest.mark.asyncio
async def test_primary_429_logs_event_then_secondary_failover_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    async def create(**kwargs: object) -> AsyncMock:
        # Primary Groq models fail with the verbatim TPD 429; the secondary model works.
        if kwargs["model"] == "sec-model":
            return _ok_response()
        raise _err_429(headers={"retry-after": "180", "x-ratelimit-remaining-tokens": "0"})

    with patch("app.core.providers.groq.AsyncGroq") as client_cls:
        client_cls.return_value.chat.completions.create = AsyncMock(side_effect=create)
        with capture_logs() as logs:
            _, model, *_ = await llm_module.extract_with_llm("prompt")

    assert model == "sec-model"
    limited = _events(logs, "llm.rate_limited")
    assert limited, "primary 429 must be visible"
    assert limited[0]["provider"] == "groq"
    assert limited[0]["model"] == "openai/gpt-oss-20b"
    assert limited[0]["tier"] == "small"
    assert limited[0]["role"] == "primary"
    assert limited[0]["limit_type"] == "TPD"
    assert limited[0]["org_id"] == "org_01abcXYZ9"
    assert limited[0]["ratelimit_headers"]["x-ratelimit-remaining-tokens"] == 0.0
    failover = _events(logs, "llm.failover")
    assert len(failover) == 1
    assert failover[0]["outcome"] == "success"
    assert failover[0]["reason"] == "rate_limited"
    assert failover[0]["from_provider"] == "groq_tiered"
    assert failover[0]["to_provider"] == "groq"
    assert failover[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_secondary_groq_429_is_also_logged_and_failover_marked_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())
    with patch("app.core.providers.groq.AsyncGroq") as client_cls:
        client_cls.return_value.chat.completions.create = AsyncMock(side_effect=_err_429())
        with capture_logs() as logs, pytest.raises(RuntimeError):
            await llm_module.extract_with_llm("prompt")

    roles = {e["role"] for e in _events(logs, "llm.rate_limited")}
    assert roles == {"primary", "secondary"}
    failover = _events(logs, "llm.failover")
    assert [e["outcome"] for e in failover] == ["failed"]
