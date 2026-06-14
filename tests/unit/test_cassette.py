"""Unit tests for the cassette record/replay layer.

All tests are fully mocked — zero live network or LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import app.core.providers.cassette as cassette_mod
import pytest
from app.core.providers.cassette import (
    CassetteEntry,
    cassette_mode,
    record,
    replay,
)
from app.core.providers.groq import GroqProvider, _make_cassette_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL = "llama-3.3-70b-versatile"
SYS = "You are a helpful assistant."
USER = "Summarise this review."
RAW = '{"sentiment": "positive"}'
TIN = 120
TOUT = 40


def _seed_cassette(tmp_path: Path, key: str, raw: str, tin: int, tout: int) -> Path:
    """Write a minimal cassette JSON to tmp_path/cassettes.json and return its path."""
    store: dict[str, CassetteEntry] = {key: {"raw": raw, "tokens_in": tin, "tokens_out": tout}}
    p = tmp_path / "cassettes.json"
    p.write_text(json.dumps(store), encoding="utf-8")
    return p


def _groq_response_mock(raw: str, tin: int = TIN, tout: int = TOUT) -> MagicMock:
    choice = MagicMock()
    choice.message.content = raw
    usage = MagicMock()
    usage.prompt_tokens = tin
    usage.completion_tokens = tout
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# cassette_mode()
# ---------------------------------------------------------------------------


class TestCassetteMode:
    def test_unset_returns_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_CASSETTE_MODE", raising=False)
        assert cassette_mode() == "live"

    def test_empty_string_returns_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "")
        assert cassette_mode() == "live"

    def test_live_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "live")
        assert cassette_mode() == "live"

    def test_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "record")
        assert cassette_mode() == "record"

    def test_replay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")
        assert cassette_mode() == "replay"

    def test_uppercase_normalised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "REPLAY")
        assert cassette_mode() == "replay"

    def test_unknown_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "bogus")
        with pytest.raises(ValueError, match="Unknown EVAL_CASSETTE_MODE"):
            cassette_mode()


# ---------------------------------------------------------------------------
# Key determinism
# ---------------------------------------------------------------------------


class TestMakeCassetteKey:
    def test_same_inputs_same_key(self) -> None:
        k1 = _make_cassette_key(MODEL, SYS, USER)
        k2 = _make_cassette_key(MODEL, SYS, USER)
        assert k1 == k2

    def test_different_model_different_key(self) -> None:
        k1 = _make_cassette_key(MODEL, SYS, USER)
        k2 = _make_cassette_key("llama-3.1-8b-instant", SYS, USER)
        assert k1 != k2

    def test_different_system_prompt_different_key(self) -> None:
        k1 = _make_cassette_key(MODEL, SYS, USER)
        k2 = _make_cassette_key(MODEL, "Different system.", USER)
        assert k1 != k2

    def test_different_user_prompt_different_key(self) -> None:
        k1 = _make_cassette_key(MODEL, SYS, USER)
        k2 = _make_cassette_key(MODEL, SYS, "Different user prompt.")
        assert k1 != k2

    def test_key_is_hex_64_chars(self) -> None:
        k = _make_cassette_key(MODEL, SYS, USER)
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)

    def test_key_is_stable_across_calls(self) -> None:
        """Calling the key function twice with identical inputs must give the same result."""
        k1 = _make_cassette_key(MODEL, SYS, USER)
        k2 = _make_cassette_key(MODEL, SYS, USER)
        assert k1 == k2


# ---------------------------------------------------------------------------
# record() / replay() store operations
# ---------------------------------------------------------------------------


class TestStoreOperations:
    def test_record_then_replay(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")
        key = _make_cassette_key(MODEL, SYS, USER)
        record(key, RAW, TIN, TOUT)
        result = replay(key)
        assert result == (RAW, TIN, TOUT)

    def test_replay_missing_key_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")
        assert replay("nonexistent-key") is None

    def test_replay_missing_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing = tmp_path / "does_not_exist.json"
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", missing)
        assert replay("any-key") is None

    def test_record_overwrites_existing_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")
        key = _make_cassette_key(MODEL, SYS, USER)
        record(key, "first", 1, 1)
        record(key, "second", 2, 2)
        result = replay(key)
        assert result == ("second", 2, 2)

    def test_record_multiple_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")
        k1 = _make_cassette_key(MODEL, SYS, "prompt A")
        k2 = _make_cassette_key(MODEL, SYS, "prompt B")
        record(k1, "raw A", 10, 5)
        record(k2, "raw B", 20, 8)
        assert replay(k1) == ("raw A", 10, 5)
        assert replay(k2) == ("raw B", 20, 8)


# ---------------------------------------------------------------------------
# GroqProvider.complete — replay mode
# ---------------------------------------------------------------------------


class TestGroqProviderReplayMode:
    @pytest.mark.asyncio
    async def test_replay_returns_cassette_no_network_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replay mode: stored tuple returned, AsyncGroq is NEVER instantiated."""
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")

        provider = GroqProvider(model=MODEL, api_key="fake")
        key = _make_cassette_key(MODEL, SYS, USER)
        _seed_cassette(tmp_path, key, RAW, TIN, TOUT)

        with patch("app.core.providers.groq.AsyncGroq") as mock_groq:
            result = await provider.complete(USER, system_prompt=SYS)
            mock_groq.assert_not_called()

        assert result == (RAW, TIN, TOUT)

    @pytest.mark.asyncio
    async def test_replay_missing_key_raises_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replay mode with no matching cassette raises a descriptive RuntimeError."""
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")

        provider = GroqProvider(model=MODEL, api_key="fake")

        with patch("app.core.providers.groq.AsyncGroq"):
            with pytest.raises(RuntimeError, match="No cassette for key"):
                await provider.complete(USER, system_prompt=SYS)

    @pytest.mark.asyncio
    async def test_replay_missing_cassette_file_raises_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replay mode with missing cassettes.json raises a descriptive RuntimeError."""
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "replay")
        missing = tmp_path / "does_not_exist.json"
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", missing)

        provider = GroqProvider(model=MODEL, api_key="fake")
        with patch("app.core.providers.groq.AsyncGroq"):
            with pytest.raises(RuntimeError, match="re-record with EVAL_CASSETTE_MODE=record"):
                await provider.complete(USER, system_prompt=SYS)


# ---------------------------------------------------------------------------
# GroqProvider.complete — record mode
# ---------------------------------------------------------------------------


class TestGroqProviderRecordMode:
    @pytest.mark.asyncio
    async def test_record_mode_saves_cassette_and_returns_tuple(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record mode: live call is made, result saved under the correct key."""
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "record")
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")

        provider = GroqProvider(model=MODEL, api_key="fake")
        mock_resp = _groq_response_mock(RAW, TIN, TOUT)

        with patch("app.core.providers.groq.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
            result = await provider.complete(USER, system_prompt=SYS)
            MockGroq.assert_called_once()

        assert result == (RAW, TIN, TOUT)

        # Verify it was saved under the correct key
        key = _make_cassette_key(MODEL, SYS, USER)
        saved = replay(key)  # reads from tmp_path since CASSETTES_PATH is patched
        assert saved == (RAW, TIN, TOUT)

    @pytest.mark.asyncio
    async def test_record_mode_with_retry_flag_still_saves_under_base_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cassette key is keyed on the base user_prompt, not the retry-suffixed version."""
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "record")
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", tmp_path / "cassettes.json")

        provider = GroqProvider(model=MODEL, api_key="fake")
        mock_resp = _groq_response_mock(RAW)

        with patch("app.core.providers.groq.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
            await provider.complete(USER, system_prompt=SYS, retry=True)

        key = _make_cassette_key(MODEL, SYS, USER)
        assert replay(key) is not None


# ---------------------------------------------------------------------------
# GroqProvider.complete — live mode (default)
# ---------------------------------------------------------------------------


class TestGroqProviderLiveMode:
    @pytest.mark.asyncio
    async def test_live_mode_calls_network_and_does_not_write_cassette(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Live mode (default): network call is made; cassette store is NOT written."""
        monkeypatch.delenv("EVAL_CASSETTE_MODE", raising=False)
        cassette_path = tmp_path / "cassettes.json"
        monkeypatch.setattr(cassette_mod, "CASSETTES_PATH", cassette_path)

        provider = GroqProvider(model=MODEL, api_key="fake")
        mock_resp = _groq_response_mock(RAW, TIN, TOUT)

        with patch("app.core.providers.groq.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
            result = await provider.complete(USER, system_prompt=SYS)
            MockGroq.assert_called_once()

        assert result == (RAW, TIN, TOUT)
        # Cassette file must NOT have been created
        assert not cassette_path.exists()

    @pytest.mark.asyncio
    async def test_live_mode_explicit_preserves_retry_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With retry=True the suffix is appended to the prompt sent to the API."""
        monkeypatch.setenv("EVAL_CASSETTE_MODE", "live")
        provider = GroqProvider(model=MODEL, api_key="fake")
        mock_resp = _groq_response_mock(RAW)
        captured_messages: list[list[dict[str, str]]] = []

        async def fake_create(**kwargs: object) -> MagicMock:
            captured_messages.append(kwargs["messages"])  # type: ignore[arg-type]
            return mock_resp

        with patch("app.core.providers.groq.AsyncGroq") as MockGroq:
            MockGroq.return_value.chat.completions.create = fake_create
            await provider.complete(USER, system_prompt=SYS, retry=True)

        user_content = captured_messages[0][1]["content"]
        assert "IMPORTANT" in user_content  # retry suffix appended
