"""Unit tests for eval/consensus/build_report.py -- pure aggregation, no live calls."""

from __future__ import annotations

from eval.consensus.build_report import (
    build_new_fixture,
    fleiss_table,
    passes_growth_gate,
    raw_votes_matrix,
    validation_agreement,
)
from eval.consensus.voting import NO_RESPONSE


def _consensus(**fields):
    return {f: {"silver": v, "agreement": a, "votes": votes} for f, (v, a, votes) in fields.items()}


class TestPassesGrowthGate:
    def test_all_gate_fields_agreeing_passes(self):
        consensus = _consensus(
            sentiment=("positive", "unanimous", {}),
            urgency=("low", "unanimous", {}),
            buy_again=(True, "unanimous", {}),
            language=("en", "unanimous", {}),
        )
        assert passes_growth_gate(consensus) is True

    def test_one_split_field_fails(self):
        consensus = _consensus(
            sentiment=("positive", "unanimous", {}),
            urgency=(None, "split", {}),
            buy_again=(True, "unanimous", {}),
            language=("en", "unanimous", {}),
        )
        assert passes_growth_gate(consensus) is False

    def test_missing_field_fails(self):
        consensus = _consensus(sentiment=("positive", "unanimous", {}))
        assert passes_growth_gate(consensus) is False


class TestRawVotesMatrix:
    def test_builds_raters_by_units_matrix(self):
        records = [
            {"consensus": {"sentiment": {"votes": {"j1": "positive", "j2": "positive"}}}},
            {"consensus": {"sentiment": {"votes": {"j1": "negative", "j2": NO_RESPONSE}}}},
        ]
        matrix = raw_votes_matrix(records, "sentiment", ["j1", "j2"])
        assert matrix == [["positive", "negative"], ["positive", None]]


class TestFleissTable:
    def test_excludes_items_with_a_non_response(self):
        records = [
            {"consensus": {"sentiment": {"votes": {"j1": "positive", "j2": "positive"}}}},
            {"consensus": {"sentiment": {"votes": {"j1": "negative", "j2": NO_RESPONSE}}}},
            {"consensus": {"sentiment": {"votes": {"j1": "negative", "j2": "negative"}}}},
        ]
        table = fleiss_table(records, "sentiment", ["j1", "j2"])
        # Only 2 of 3 items have full coverage; total rater count per row == 2.
        assert len(table) == 2
        assert all(sum(row) == 2 for row in table)


class TestValidationAgreement:
    def test_agreement_rate_computed_per_field(self):
        existing = [
            {"id": "f1", "ground_truth": {"sentiment": "positive"}},
            {"id": "f2", "ground_truth": {"sentiment": "negative"}},
        ]
        consensus_by_id = {
            "f1": {"consensus": {"sentiment": {"silver": "positive", "agreement": "unanimous"}}},
            "f2": {"consensus": {"sentiment": {"silver": "neutral", "agreement": "unanimous"}}},
        }
        result = validation_agreement(existing, consensus_by_id, fields=("sentiment",))
        assert result["n_fixtures_scored"] == 2
        assert result["per_field"]["sentiment"]["n_compared"] == 2
        assert result["per_field"]["sentiment"]["n_agree"] == 1
        assert result["per_field"]["sentiment"]["agreement_rate"] == 0.5

    def test_split_consensus_excluded_from_comparison(self):
        existing = [{"id": "f1", "ground_truth": {"sentiment": "positive"}}]
        consensus_by_id = {
            "f1": {"consensus": {"sentiment": {"silver": None, "agreement": "split"}}},
        }
        result = validation_agreement(existing, consensus_by_id, fields=("sentiment",))
        assert result["per_field"]["sentiment"]["n_compared"] == 0
        assert result["per_field"]["sentiment"]["n_no_consensus"] == 1
        assert result["per_field"]["sentiment"]["agreement_rate"] is None

    def test_missing_consensus_record_skipped(self):
        existing = [{"id": "f1", "ground_truth": {"sentiment": "positive"}}]
        result = validation_agreement(existing, {}, fields=("sentiment",))
        assert result["n_fixtures_scored"] == 0


class TestBuildNewFixture:
    def test_assembles_expected_schema(self):
        consensus = _consensus(
            product=("Vacuum", "unanimous", {}),
            stars=(None, "unanimous", {}),
            stars_inferred=(4, "unanimous", {}),
            pros=(["strong suction"], "unanimous", {}),
            cons=(["short battery"], "unanimous", {}),
            buy_again=(True, "unanimous", {}),
            sentiment=("positive", "unanimous", {}),
            topics=(["suction"], "unanimous", {}),
            competitor_mentions=([], "unanimous", {}),
            urgency=("low", "unanimous", {}),
            feature_requests=([], "unanimous", {}),
            language=("en", "unanimous", {}),
        )
        fixture = build_new_fixture("grow-en-0001", "Great vacuum", consensus, "flipkart/x")
        assert fixture["id"] == "grow-en-0001"
        assert fixture["review_text"] == "Great vacuum"
        assert fixture["ground_truth"]["sentiment"] == "positive"
        assert fixture["ground_truth"]["product"] == "Vacuum"
        assert fixture["scoring_notes"]["tolerance_fields"] == {"stars_inferred": 1}
        assert fixture["labeling_meta"]["labeled_by"] == "multi-llm-consensus"
        assert fixture["labeling_meta"]["agreement_per_field"]["sentiment"] == "unanimous"

    def test_falsy_product_defaults_to_unknown(self):
        consensus = _consensus(product=(None, "split", {}), sentiment=("positive", "unanimous", {}))
        fixture = build_new_fixture("grow-en-0002", "text", consensus, "src")
        assert fixture["ground_truth"]["product"] == "unknown"
