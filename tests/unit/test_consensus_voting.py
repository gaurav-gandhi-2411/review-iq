"""Unit tests for eval/consensus/voting.py -- per-field unanimous/majority/split voting."""

from __future__ import annotations

from eval.consensus.voting import (
    consensus_for_item,
    vote_list_overlap,
    vote_scalar_exact,
    vote_scalar_tolerant,
)


class TestVoteScalarExact:
    def test_unanimous(self):
        silver, level = vote_scalar_exact({"a": "positive", "b": "positive", "c": "positive"})
        assert silver == "positive"
        assert level == "unanimous"

    def test_majority(self):
        silver, level = vote_scalar_exact({"a": "positive", "b": "positive", "c": "negative"})
        assert silver == "positive"
        assert level == "majority"

    def test_split_three_way_disagreement(self):
        silver, level = vote_scalar_exact({"a": "positive", "b": "negative", "c": "neutral"})
        assert silver is None
        assert level == "split"

    def test_insufficient_when_fewer_than_two_respond(self):
        silver, level = vote_scalar_exact({"a": "positive", "b": None, "c": None})
        assert silver is None
        assert level == "insufficient"

    def test_normalized_case_insensitive_match(self):
        silver, level = vote_scalar_exact(
            {"a": "Vacuum Cleaner", "b": "vacuum cleaner", "c": "Blender"}, normalize=True
        )
        assert level == "majority"
        # Silver keeps ORIGINAL casing from an agreeing judge, not the normalized key.
        assert silver in ("Vacuum Cleaner", "vacuum cleaner")

    def test_boolean_field_unanimous(self):
        silver, level = vote_scalar_exact({"a": True, "b": True, "c": True})
        assert silver is True
        assert level == "unanimous"


class TestVoteScalarTolerant:
    def test_all_within_tolerance_is_unanimous(self):
        silver, level = vote_scalar_tolerant({"a": 3, "b": 4, "c": 3}, tolerance=1)
        assert level == "unanimous"
        assert silver == 3

    def test_two_within_tolerance_one_outlier_is_majority(self):
        silver, level = vote_scalar_tolerant({"a": 4, "b": 4, "c": 1}, tolerance=1)
        assert level == "majority"
        assert silver == 4

    def test_all_far_apart_is_split(self):
        silver, level = vote_scalar_tolerant({"a": 1, "b": 3, "c": 5}, tolerance=1)
        assert silver is None
        assert level == "split"

    def test_insufficient_with_one_response(self):
        silver, level = vote_scalar_tolerant({"a": 3, "b": None, "c": None}, tolerance=1)
        assert level == "insufficient"


class TestVoteListOverlap:
    def test_unanimous_high_overlap(self):
        values = {
            "a": ["good sound", "long battery"],
            "b": ["good sound", "long battery life"],
            "c": ["great sound", "long lasting battery"],
        }
        silver, level = vote_list_overlap(values, threshold=0.3)
        assert level in ("unanimous", "majority")
        assert silver is not None

    def test_split_when_no_overlap(self):
        values = {
            "a": ["good sound"],
            "b": ["cheap price"],
            "c": ["fast delivery"],
        }
        silver, level = vote_list_overlap(values, threshold=0.5)
        assert silver is None
        assert level == "split"

    def test_two_empty_lists_agree(self):
        # Two judges both saying "no cons" should count as agreement, not a mismatch.
        values = {"a": [], "b": [], "c": ["minor issue"]}
        silver, level = vote_list_overlap(values, threshold=0.5)
        assert level in ("majority", "unanimous")
        assert silver == []

    def test_insufficient_with_one_response(self):
        silver, level = vote_list_overlap({"a": ["x"], "b": None, "c": None})
        assert level == "insufficient"


class TestConsensusForItem:
    def test_produces_all_expected_fields(self):
        judge_outputs = {
            "j1": {
                "product": "Vacuum",
                "stars": None,
                "stars_inferred": 4,
                "pros": ["strong suction"],
                "cons": ["short battery"],
                "buy_again": True,
                "sentiment": "positive",
                "topics": ["suction", "battery"],
                "competitor_mentions": [],
                "urgency": "low",
                "feature_requests": [],
                "language": "en",
            },
            "j2": {
                "product": "Vacuum",
                "stars": None,
                "stars_inferred": 4,
                "pros": ["powerful suction"],
                "cons": ["battery dies fast"],
                "buy_again": True,
                "sentiment": "positive",
                "topics": ["suction", "battery"],
                "competitor_mentions": [],
                "urgency": "low",
                "feature_requests": [],
                "language": "en",
            },
            "j3": {
                "product": "Vacuum",
                "stars": None,
                "stars_inferred": 3,
                "pros": ["good suction"],
                "cons": ["poor battery life"],
                "buy_again": True,
                "sentiment": "positive",
                "topics": ["suction", "battery"],
                "competitor_mentions": [],
                "urgency": "low",
                "feature_requests": [],
                "language": "en",
            },
        }
        result = consensus_for_item(judge_outputs)
        for field in (
            "product",
            "stars",
            "stars_inferred",
            "buy_again",
            "sentiment",
            "language",
            "urgency",
            "pros",
            "cons",
            "topics",
            "feature_requests",
            "competitor_mentions",
        ):
            assert field in result
            assert "silver" in result[field]
            assert "agreement" in result[field]
        assert result["sentiment"]["agreement"] == "unanimous"
        assert result["stars_inferred"]["agreement"] == "unanimous"  # 4,4,3 within tolerance=1

    def test_missing_judge_output_handled_as_none(self):
        judge_outputs = {
            "j1": {"sentiment": "positive"},
            "j2": {"sentiment": "positive"},
            "j3": None,  # judge errored entirely
        }
        result = consensus_for_item(judge_outputs)
        assert result["sentiment"]["silver"] == "positive"
        # Only 2 of 3 judges responded, but both of THEM agree -- unanimous among
        # responders, not "majority" (majority implies a responding judge dissented).
        assert result["sentiment"]["agreement"] == "unanimous"
