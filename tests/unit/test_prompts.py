"""Unit tests for language-branched prompt builder."""

from __future__ import annotations

import pytest

from app.core.prompts import PROMPT_VERSION, build_prompt


class TestPromptVersion:
    def test_version_is_v2(self) -> None:
        assert PROMPT_VERSION == "v2.0"


class TestBuildPromptDispatch:
    def test_english_prompt_returned_for_en(self) -> None:
        result = build_prompt("<review>Great product!</review>", "en")
        assert "<review>Great product!</review>" in result
        assert "language" in result.lower()

    def test_hinglish_prompt_returned_for_hi_en(self) -> None:
        result = build_prompt("<review>Bahut achha hai yaar</review>", "hi-en")
        assert "<review>Bahut achha hai yaar</review>" in result
        assert "Hinglish" in result

    def test_hindi_prompt_returned_for_hi(self) -> None:
        result = build_prompt("<review>बहुत अच्छा</review>", "hi")
        assert "<review>बहुत अच्छा</review>" in result
        assert "Devanagari" in result or "Hindi" in result

    def test_unknown_language_falls_back_to_english(self) -> None:
        result = build_prompt("<review>Test</review>", "ta")
        # Should not raise; returns English prompt
        assert "<review>Test</review>" in result

    def test_other_language_falls_back_to_english(self) -> None:
        result = build_prompt("<review>Test</review>", "other")
        assert "<review>Test</review>" in result

    def test_empty_language_falls_back_to_english(self) -> None:
        result = build_prompt("<review>Test</review>", "")
        assert "<review>Test</review>" in result

    def test_default_language_is_english(self) -> None:
        result = build_prompt("<review>Test</review>")
        assert "<review>Test</review>" in result


class TestEnglishPromptContent:
    def test_stars_null_rule_present(self) -> None:
        prompt = build_prompt("<review>x</review>", "en")
        assert "NULL" in prompt or "null" in prompt.lower()

    def test_all_fields_mentioned(self) -> None:
        prompt = build_prompt("<review>x</review>", "en")
        for field in ("product", "stars", "pros", "cons", "buy_again", "sentiment",
                      "topics", "urgency", "feature_requests", "language"):
            assert field in prompt, f"Field '{field}' missing from English prompt"

    def test_json_only_instruction(self) -> None:
        prompt = build_prompt("<review>x</review>", "en")
        assert "JSON" in prompt


class TestHinglishPromptContent:
    def test_translate_instruction_present(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi-en")
        assert "English" in prompt
        assert "translate" in prompt.lower() or "Translate" in prompt

    def test_hinglish_example_present(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi-en")
        assert "hi-en" in prompt

    def test_all_fields_mentioned(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi-en")
        for field in ("product", "stars", "pros", "cons", "sentiment", "urgency"):
            assert field in prompt, f"Field '{field}' missing from hi-en prompt"


class TestHindiPromptContent:
    def test_translate_instruction_present(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi")
        assert "English" in prompt
        assert "translate" in prompt.lower() or "Translate" in prompt

    def test_hindi_example_present(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi")
        assert "hi" in prompt

    def test_devanagari_example_present(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi")
        # Hindi examples should contain Devanagari characters
        import re
        assert re.search(r"[ऀ-ॿ]", prompt), "Hindi prompt should include a Devanagari example"

    def test_all_fields_mentioned(self) -> None:
        prompt = build_prompt("<review>x</review>", "hi")
        for field in ("product", "stars", "pros", "cons", "sentiment", "urgency"):
            assert field in prompt, f"Field '{field}' missing from hi prompt"
