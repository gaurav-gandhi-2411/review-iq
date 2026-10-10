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
from eval.heldout_exposure import (
    BENCHMARK_GOLD,
    DEV_FIXTURE_GLOBS,
    HELD_OUT_DIR,
)

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


class TestEquivalence:
    def test_product_null_spellings_collapse(self):
        assert panel2.equivalent("product", "unknown product", "product")
        assert not panel2.equivalent("product", "earphone", "headphones")

    def test_topics_use_adr0030_canonical_form(self):
        assert panel2.equivalent("topics", ["battery_life"], ["battery"])
        assert not panel2.equivalent("topics", ["battery"], ["price"])

    def test_pros_jaccard_threshold(self):
        assert panel2.equivalent("pros", ["good sound", "cheap"], ["good sound"])  # 0.5
        assert not panel2.equivalent("pros", ["a", "b", "c"], ["a"])  # 0.33

    def test_stars_within_one(self):
        assert panel2.equivalent("stars_inferred", 4, 5)
        assert not panel2.equivalent("stars_inferred", 3, 5)


class TestResolveField:
    def test_unanimous_majority_split(self):
        outs = {
            J[0]: _out(sentiment="positive"),
            J[1]: _out(sentiment="positive"),
            J[2]: _out(sentiment="positive"),
        }
        assert panel2.resolve_field("sentiment", outs)["level"] == "unanimous"
        outs[J[2]] = _out(sentiment="negative")
        r = panel2.resolve_field("sentiment", outs)
        assert (r["level"], r["silver"]) == ("majority", "positive")
        outs[J[1]] = _out(sentiment="mixed")
        assert panel2.resolve_field("sentiment", outs)["level"] == "split"

    def test_no_response_blocks_unanimity(self):
        outs = {J[0]: _out(), J[1]: _out(), J[2]: None}
        assert panel2.resolve_field("sentiment", outs)["level"] == "majority"
        outs[J[1]] = None
        assert panel2.resolve_field("sentiment", outs)["level"] == "insufficient"

    def test_product_resolved_via_adr0030_but_not_by_panel1_literal(self):
        outs = {
            J[0]: _out(product="unknown"),
            J[1]: _out(product="product"),
            J[2]: _out(product="general product"),
        }
        assert panel2.resolve_field("product", outs, "adr0030")["level"] == "unanimous"
        assert panel2.resolve_field("product", outs, "panel1_literal")["level"] == "split"

    def test_list_silver_is_longest_agreeing(self):
        outs = {J[0]: _out(pros=["a b", "c d"]), J[1]: _out(pros=["a b"]), J[2]: _out(pros=["x"])}
        r = panel2.resolve_field("pros", outs)
        assert r["level"] == "majority" and r["silver"] == ["a b", "c d"]


class TestAgreement:
    def test_perfect_agreement_stats(self):
        per_review = {
            f"r{i}": {j: _out(sentiment=s) for j in J}
            for i, s in enumerate(["positive", "negative", "mixed", "positive"])
        }
        stats = panel2.field_agreement(per_review, list(per_review), J)
        assert stats["sentiment"]["alpha"] == pytest.approx(1.0)
        assert stats["sentiment"]["fleiss_kappa"] == pytest.approx(1.0)

    def test_disagreement_lowers_alpha(self):
        per_review = {
            "a": {
                J[0]: _out(sentiment="positive"),
                J[1]: _out(sentiment="negative"),
                J[2]: _out(sentiment="positive"),
            },
            "b": {
                J[0]: _out(sentiment="negative"),
                J[1]: _out(sentiment="negative"),
                J[2]: _out(sentiment="negative"),
            },
            "c": {
                J[0]: _out(sentiment="mixed"),
                J[1]: _out(sentiment="mixed"),
                J[2]: _out(sentiment="positive"),
            },
        }
        stats = panel2.field_agreement(per_review, list(per_review), J)
        assert stats["sentiment"]["alpha"] < 1.0


class TestSelectActivePanel:
    def _res(self, misses, cost=0.001):
        return {"misses": misses, "mean_cost_per_call": cost}

    def test_picks_three_best_distinct_vendors(self):
        results = {
            "deepseek/deepseek-v4-flash": self._res(0),
            "nvidia/nemotron-3-super-120b-a12b": self._res(1),
            "mistralai/mistral-small-2603": self._res(2),
            "z-ai/glm-4.7-flash": self._res(5),
            "thinkingmachines/inkling-small": self._res(3),
        }
        assert panel2.select_active_panel(results) == [
            "deepseek/deepseek-v4-flash",
            "nvidia/nemotron-3-super-120b-a12b",
            "mistralai/mistral-small-2603",
        ]

    def test_fewer_than_three_passing_returns_empty(self):
        results = {m: self._res(9) for m in J}
        results[J[0]] = self._res(0)
        assert panel2.select_active_panel(results) == []

    def test_same_vendor_twice_counts_once(self):
        results = {
            "deepseek/deepseek-v4-flash": self._res(0),
            "deepseek/deepseek-v4-pro": self._res(0),
            "mistralai/mistral-small-2603": self._res(1),
        }
        assert panel2.select_active_panel(results) == []


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


class TestHinglishControlSet:
    items = json.loads(panel2.HINGLISH_CONTROL_PATH.read_text(encoding="utf-8"))

    def test_shape(self):
        assert len(self.items) == 12
        for it in self.items:
            assert it["why_unambiguous"].strip()
            assert it["expected"]
            assert it["id"].startswith("calh-")

    def test_no_sentence_occurs_in_any_corpus_fixture(self):
        corpus: list[str] = []
        for p in HELD_OUT_DIR.glob("*.json"):
            d = json.loads(p.read_text(encoding="utf-8"))
            corpus.append(d.get("review_text", ""))
        for pattern in DEV_FIXTURE_GLOBS:
            for p in ROOT.glob(pattern):
                corpus.append(json.loads(p.read_text(encoding="utf-8")).get("review_text", ""))
        if BENCHMARK_GOLD.exists():
            for line in BENCHMARK_GOLD.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    corpus.append(json.loads(line)["text"])
        blob = [normalize_review_text(t) for t in corpus]
        assert len(blob) > 100  # the corpora were actually loaded
        for it in self.items:
            whole = normalize_review_text(it["text"])
            assert not any(whole in b for b in blob), it["id"]
            for sent in re.split(r"[.!?।]+", it["text"]):
                key = normalize_review_text(sent)
                # very short sentences ("Paisa vasool!") are common phrases, not copies.
                if len(key) >= 20:
                    assert not any(key in b for b in blob), (it["id"], sent)
