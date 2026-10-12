"""Unit tests for eval/consensus/panel2.py and heldout_unscored.adjudicated_block.

Fakes only: no network, no live LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from eval.consensus import panel, panel2

ROOT = Path(__file__).resolve().parents[2]


def normalize_review_text(text: str) -> str:
    # Unicode-aware: the repo's ASCII-only normaliser maps Devanagari text to the empty string,
    # which is a substring of everything and would make this check vacuous or always failing.
    return re.sub(r"[\W_]+", "", text.lower().replace("read more", ""))


J = [
    "deepseek/deepseek-v4-flash",
    "nvidia/nemotron-3-super-120b-a12b",
    "mistralai/mistral-small-2603",
]


def _out(**kw):
    base = {
        "product": "earphone",
        "stars_inferred": 4,
        "pros": ["good sound"],
        "cons": [],
        "buy_again": None,
        "sentiment": "positive",
        "topics": ["sound_quality"],
        "competitor_mentions": [],
    }
    base.update(kw)
    return base


class TestRoster:
    def test_candidates_pass_the_guard_with_panel1_exclusion(self, monkeypatch):
        class S:
            groq_model_small = "openai/gpt-oss-20b"
            groq_model_large = "openai/gpt-oss-120b"
            secondary_provider_model = "meta-llama/llama-3.3-70b-instruct"
            gemini_model = "gemini-2.5-flash"

        monkeypatch.setattr("app.core.config.get_settings", lambda: S())
        panel.assert_no_self_judging(
            tuple(panel2.CANDIDATES), extra_forbidden_families=panel2.PANEL1_FORBIDDEN_FAMILIES
        )

    def test_panel1_vendor_candidate_rejected(self, monkeypatch):
        class S:
            groq_model_small = "openai/gpt-oss-20b"
            groq_model_large = "openai/gpt-oss-120b"
            secondary_provider_model = ""
            gemini_model = "gemini-2.5-flash"

        monkeypatch.setattr("app.core.config.get_settings", lambda: S())
        with pytest.raises(ValueError, match="panel-1 vendor"):
            panel.assert_no_self_judging(
                ({"id": "qwen/qwen3.9-27b"},),
                extra_forbidden_families=panel2.PANEL1_FORBIDDEN_FAMILIES,
            )


class TestRunnerReplayAndBudget:
    def _runner(self, tmp_path, mode, entries=None):
        cas = panel2.Cassette(tmp_path / "c.json")
        for (model, text, rep), entry in (entries or {}).items():
            cas.store[panel2.cassette_key(model, text, rep)] = entry
        return panel2.Runner(mode=mode, cassette=cas, ledger=panel2.Ledger())

    def test_replay_serves_cassette_with_zero_network_and_records_cost(self, tmp_path):
        raw = json.dumps(_out())
        r = self._runner(
            tmp_path,
            "replay",
            {
                (J[0], "t", 0): {
                    "raw": raw,
                    "tokens_in": 10,
                    "tokens_out": 5,
                    "cost_usd": 0.001,
                    "status": 200,
                }
            },
        )
        v = asyncio.run(r.judge("calibration", "u1", J[0], "t"))
        assert v["parsed"]["sentiment"] == "positive" and v["cost_usd"] == 0.001
        assert r.ledger.summary()["total_cost_usd"] == 0.001
        assert r.ledger.live_total == 0.0

    def test_replay_missing_entry_fails_loud(self, tmp_path):
        r = self._runner(tmp_path, "replay")
        with pytest.raises(panel2.MissingCassetteError):
            asyncio.run(r.judge("calibration", "u1", J[0], "t"))

    def test_budget_cap_stops_before_the_call(self, tmp_path):
        r = self._runner(tmp_path, "record")
        r.ledger.live_cost["x"] = 2.0
        with pytest.raises(panel2.BudgetExceededError):
            asyncio.run(r.judge("calibration", "u1", J[0], "t"))

    def test_retryable_cached_error_is_retried_in_record_mode(self, tmp_path, monkeypatch):
        calls = []

        async def fake_live(http, key, model, text):
            calls.append(model)
            return {
                "raw": json.dumps(_out()),
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.0,
                "status": 200,
            }

        monkeypatch.setattr(panel2, "_live_call", fake_live)
        r = self._runner(
            tmp_path,
            "record",
            {(J[0], "t", 0): {"raw": "", "status": 503, "error": "x", "retryable": True}},
        )
        v = asyncio.run(r.judge("calibration", "u1", J[0], "t"))
        assert calls == [J[0]] and v["parsed"] is not None

    def test_200_with_unparseable_content_is_never_retried(self, tmp_path, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("must not call live")

        monkeypatch.setattr(panel2, "_live_call", boom)
        r = self._runner(
            tmp_path,
            "record",
            {
                (J[0], "t", 0): {
                    "raw": "not json",
                    "status": 200,
                    "tokens_in": 1,
                    "tokens_out": 1,
                    "cost_usd": 0.0,
                }
            },
        )
        v = asyncio.run(r.judge("calibration", "u1", J[0], "t"))
        assert v["parsed"] is None


class TestParseRaw:
    def test_think_block_and_prose_tolerated(self):
        raw = "<think>hmm</think>Here you go: " + json.dumps(_out())
        assert panel2.parse_raw(raw)["sentiment"] == "positive"

    def test_garbage_is_none(self):
        assert panel2.parse_raw("no json here") is None
        assert panel2.parse_raw("") is None
