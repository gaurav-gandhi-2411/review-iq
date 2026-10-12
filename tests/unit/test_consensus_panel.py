"""Unit tests for eval/consensus/panel.py -- pure logic + the self-judging guard.

No live LLM calls (call_judge/make_groq_client are exercised in integration/consensus runs
only, not here).
"""

from __future__ import annotations

import pytest
from eval.consensus import panel


class FakeSettings:
    groq_model_small = "openai/gpt-oss-20b"
    groq_model_large = "openai/gpt-oss-120b"
    secondary_provider_model = "meta-llama/llama-3.3-70b-instruct"
    gemini_model = "gemini-2.5-flash"


def _roster(*ids: str) -> tuple[dict[str, str], ...]:
    return tuple({"id": i, "provider": "x", "family": "x", "owner": "x"} for i in ids)


@pytest.fixture(autouse=True)
def _fake_settings(monkeypatch):
    monkeypatch.setattr("app.core.config.get_settings", lambda: FakeSettings())


class TestModelFamily:
    @pytest.mark.parametrize(
        ("model_id", "family"),
        [
            ("openai/gpt-oss-120b", "openai"),
            ("meta-llama/llama-3.1-8b-instruct", "meta"),
            ("llama-3.3-70b-versatile", "meta"),
            ("gemini-3.5-flash-lite", "google"),
            ("google/gemma-3-27b-it", "google"),
            ("qwen/qwen3.6-27b", "alibaba"),
            ("deepseek/deepseek-v4-flash", "deepseek"),
            ("nvidia/nemotron-3-super-120b-a12b", "nvidia"),
            ("mistralai/mistral-small-2603", "mistral"),
            ("z-ai/glm-4.7-flash", "zhipu"),
            ("thinkingmachines/inkling-small", "thinkingmachines"),
            ("allam-2-7b", "sdaia"),
        ],
    )
    def test_known(self, model_id, family):
        assert panel.model_family(model_id) == family

    def test_unknown_fails_closed_with_actionable_message(self):
        with pytest.raises(ValueError, match="add its organisation to _ORG_FAMILY"):
            panel.model_family("brandnew/model-1")
        with pytest.raises(ValueError, match="Unknown vendor family"):
            panel.model_family("mystery-model-9")


class TestAssertNoSelfJudging:
    def test_clean_roster_raises_nothing(self):
        panel.assert_no_self_judging(_roster("qwen/qwen3.6-27b", "allam-2-7b"))

    def test_deepseek_accepted(self):
        panel.assert_no_self_judging(_roster("deepseek/deepseek-v4-flash"))

    def test_real_panel1_roster_passes_after_the_google_judge_was_retired(self, monkeypatch):
        # S17 X3: the real JUDGE_MODELS must pass the guard against the REAL Settings.
        monkeypatch.undo()
        panel.assert_no_self_judging()
        assert all(m["provider"] != "gemini" for m in panel.JUDGE_MODELS)

    def test_retired_google_judge_is_data_only_and_would_be_rejected_if_google_returns(self):
        # Data-driven guard: if a Google model is a production path again, the retired judge
        # is rejected without anyone editing the guard.
        assert [m["id"] for m in panel.RETIRED_JUDGE_MODELS] == ["gemini-3.5-flash-lite"]
        with pytest.raises(ValueError, match="gemini-3.5-flash-lite"):
            panel.assert_no_self_judging(panel.RETIRED_JUDGE_MODELS)  # FakeSettings has gemini

    def test_production_model_ids_is_settings_driven(self, monkeypatch):
        class S(FakeSettings):
            some_new_provider_model = "acme/brand-new-1"

        monkeypatch.setattr("app.core.config.get_settings", lambda: S())
        ids = panel.production_model_ids()
        assert ids["some_new_provider_model"] == "acme/brand-new-1"
        assert ids["gemini_model"] == "gemini-2.5-flash"

    def test_real_settings_have_no_gemini_field(self):
        from app.core.config import Settings

        assert not [f for f in Settings.model_fields if "gemini" in f.lower()]

    @pytest.mark.parametrize(
        "bad",
        [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "openai/gpt-5-mini",
            "meta-llama/llama-3.1-8b-instruct",
            "meta-llama/llama-3.3-70b-instruct",
            "llama-3.3-70b-versatile",
            "gemini-3.5-flash-lite",
            "gemini-2.5-flash",
            "google/gemma-3-27b-it",
        ],
    )
    def test_production_vendor_rejected(self, bad):
        with pytest.raises(ValueError, match="Self-judging conflict"):
            panel.assert_no_self_judging(_roster("qwen/qwen3.6-27b", bad))

    def test_llama_rejected_even_when_secondary_env_unset(self, monkeypatch):
        class Unset(FakeSettings):
            secondary_provider_model = ""

        monkeypatch.setattr("app.core.config.get_settings", lambda: Unset())
        with pytest.raises(ValueError, match="meta"):
            panel.assert_no_self_judging(_roster("meta-llama/llama-3.1-8b-instruct"))

    def test_unknown_family_fails_closed(self):
        with pytest.raises(ValueError, match="Unknown vendor family"):
            panel.assert_no_self_judging(_roster("newvendor/shiny-1"))

    def test_extra_forbidden_family_names_reason(self):
        with pytest.raises(ValueError, match="panel-1 vendor"):
            panel.assert_no_self_judging(
                _roster("qwen/qwen3.6-27b"),
                extra_forbidden_families={"alibaba": "panel-1 vendor"},
            )
