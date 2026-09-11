"""Unit tests for eval/consensus/panel.py -- pure logic + the self-judging guard.

No live LLM calls (call_judge/make_groq_client are exercised in integration/consensus runs
only, not here).
"""

from __future__ import annotations

from eval.consensus import panel


class TestAssertNoSelfJudging:
    def test_clean_roster_raises_nothing(self, monkeypatch):
        class FakeSettings:
            groq_model_small = "openai/gpt-oss-20b"
            groq_model_large = "openai/gpt-oss-120b"

        monkeypatch.setattr("app.core.config.get_settings", lambda: FakeSettings())
        clean_roster = (
            {
                "id": "qwen/qwen3.6-27b",
                "provider": "groq",
                "family": "Alibaba Qwen",
                "owner": "Alibaba Cloud",
            },
            {"id": "allam-2-7b", "provider": "groq", "family": "SDAIA ALLaM", "owner": "SDAIA"},
        )
        panel.assert_no_self_judging(clean_roster)  # must not raise

    def test_current_judge_models_pass_against_real_production_config(self):
        # Regression test (Session 8 P2): this is the exact incident -- the real
        # JUDGE_MODELS tuple, checked against the REAL production settings, must never
        # contain a self-judging conflict. `openai/gpt-oss-120b` was removed from
        # JUDGE_MODELS for exactly this reason; this test locks in that it stays gone.
        panel.assert_no_self_judging()  # uses real JUDGE_MODELS + real get_settings()

    def test_production_large_model_on_roster_raises(self, monkeypatch):
        class FakeSettings:
            groq_model_small = "openai/gpt-oss-20b"
            groq_model_large = "openai/gpt-oss-120b"

        monkeypatch.setattr("app.core.config.get_settings", lambda: FakeSettings())
        contaminated_roster = (
            {
                "id": "openai/gpt-oss-120b",
                "provider": "groq",
                "family": "OpenAI GPT-OSS",
                "owner": "OpenAI",
            },
            {
                "id": "qwen/qwen3.6-27b",
                "provider": "groq",
                "family": "Alibaba Qwen",
                "owner": "Alibaba Cloud",
            },
        )
        try:
            panel.assert_no_self_judging(contaminated_roster)
            raised = False
        except ValueError as e:
            raised = True
            assert "openai/gpt-oss-120b" in str(e)
        assert raised, "expected a self-judging ValueError, none was raised"

    def test_production_small_model_on_roster_also_raises(self, monkeypatch):
        class FakeSettings:
            groq_model_small = "openai/gpt-oss-20b"
            groq_model_large = "openai/gpt-oss-120b"

        monkeypatch.setattr("app.core.config.get_settings", lambda: FakeSettings())
        contaminated_roster = (
            {
                "id": "openai/gpt-oss-20b",
                "provider": "groq",
                "family": "OpenAI GPT-OSS",
                "owner": "OpenAI",
            },
        )
        try:
            panel.assert_no_self_judging(contaminated_roster)
            raised = False
        except ValueError:
            raised = True
        assert raised, "expected a self-judging ValueError for the SMALL production model too"
