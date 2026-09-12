"""Unit tests for scripts/p2_panel_contamination_check.py.

Locks in the corrected Session 8 P2 numbers cited in
docs/architecture/adr/0013-consensus-panel-self-judging-contamination.md and
docs/specs/wave1-coverage-abstention-analysis.md's correction section.
"""

from __future__ import annotations

import json

from eval.consensus.panel import JUDGE_MODELS
from scripts.p2_panel_contamination_check import load_consensus_by_id


class TestPanelCompositionMatchesProduction:
    def test_contaminated_judge_is_no_longer_a_candidate(self):
        # Session 8 P2 fix: openai/gpt-oss-120b removed from JUDGE_MODELS entirely.
        judge_ids = {m["id"] for m in JUDGE_MODELS}
        assert "openai/gpt-oss-120b" not in judge_ids

    def test_disjoint_judge_still_a_candidate(self):
        judge_ids = {m["id"] for m in JUDGE_MODELS}
        assert "qwen/qwen3.6-27b" in judge_ids


class TestDisjointJudgeReCheckMatchesRealData:
    def test_corrected_decidability_counts(self):
        # This test intentionally re-derives the check against the ALREADY-COMMITTED
        # consensus data (not the current, fixed JUDGE_MODELS) -- it verifies the
        # historical finding that motivated the fix, not the fix's own configuration.
        with open("eval/results/latest.json", encoding="utf-8") as f:
            results = json.load(f)
        consensus_by_id = load_consensus_by_id()

        HEDGE_VALUE = {"buy_again": None, "sentiment": "mixed"}
        contaminated_judge = "openai/gpt-oss-120b"
        disjoint_judge = "qwen/qwen3.6-27b"

        counts: dict[str, dict[str, int]] = {}
        for field, hedge_value in HEDGE_VALUE.items():
            decidable = 0
            ambiguous = 0
            for fx in results["fixtures"]:
                for f in fx["fields"]:
                    if f["field"] != field or f["predicted"] != hedge_value:
                        continue
                    if f["expected"] == hedge_value:
                        continue
                    vote = consensus_by_id[fx["id"]]["consensus"][field]["votes"][disjoint_judge]
                    if vote == hedge_value:
                        ambiguous += 1
                    else:
                        decidable += 1
            counts[field] = {"decidable": decidable, "ambiguous": ambiguous}

        assert counts["buy_again"] == {"decidable": 9, "ambiguous": 1}
        assert counts["sentiment"] == {"decidable": 4, "ambiguous": 5}
        # Sanity: the contaminated judge's vote matched the model's own hedge in every
        # sentiment real-hedge case it saw -- the specific number cited in ADR 0013.
        contaminated_matches_hedge = 0
        sentiment_real_hedge_n = 0
        for fx in results["fixtures"]:
            for f in fx["fields"]:
                if (
                    f["field"] != "sentiment"
                    or f["predicted"] != "mixed"
                    or f["expected"] == "mixed"
                ):
                    continue
                sentiment_real_hedge_n += 1
                if (
                    consensus_by_id[fx["id"]]["consensus"]["sentiment"]["votes"][contaminated_judge]
                    == "mixed"
                ):
                    contaminated_matches_hedge += 1
        assert sentiment_real_hedge_n == 9
        assert contaminated_matches_hedge == 9
