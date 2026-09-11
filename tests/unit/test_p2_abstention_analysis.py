"""Unit tests for scripts/p2_abstention_analysis.py."""

from __future__ import annotations

import json
from pathlib import Path

import scripts.p2_abstention_analysis as p2


def _write_results(path: Path, fixtures: list[dict]) -> None:
    path.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")


def _write_consensus(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


class TestClassifyAbstentions:
    def test_hedge_equal_to_ground_truth_is_already_correct_not_a_hedge(
        self, tmp_path, monkeypatch
    ):
        results = tmp_path / "results.json"
        consensus = tmp_path / "consensus.jsonl"
        _write_results(
            results,
            [
                {
                    "id": "f1",
                    "language": "en",
                    "fields": [
                        {"field": "sentiment", "predicted": "mixed", "expected": "mixed"},
                    ],
                }
            ],
        )
        _write_consensus(consensus, [])
        monkeypatch.setattr(p2, "RESULTS_PATH", results)
        monkeypatch.setattr(p2, "CONSENSUS_LABELS_PATH", consensus)

        real_hedges, already_correct = p2.classify_abstentions()
        assert real_hedges == []
        assert already_correct == [("sentiment", "en", "f1")]

    def test_real_hedge_with_unanimous_panel_is_decidable_but_hedged(self, tmp_path, monkeypatch):
        results = tmp_path / "results.json"
        consensus = tmp_path / "consensus.jsonl"
        _write_results(
            results,
            [
                {
                    "id": "f1",
                    "language": "en",
                    "fields": [
                        {"field": "buy_again", "predicted": None, "expected": True},
                    ],
                }
            ],
        )
        _write_consensus(
            consensus,
            [
                {
                    "id": "f1",
                    "mode": "validate",
                    "consensus": {
                        "buy_again": {
                            "silver": True,
                            "agreement": "unanimous",
                            "votes": {"judge_a": True, "judge_b": True},
                        }
                    },
                }
            ],
        )
        monkeypatch.setattr(p2, "RESULTS_PATH", results)
        monkeypatch.setattr(p2, "CONSENSUS_LABELS_PATH", consensus)

        real_hedges, already_correct = p2.classify_abstentions()
        assert already_correct == []
        assert len(real_hedges) == 1
        assert real_hedges[0]["classification"] == "decidable_but_hedged"

    def test_real_hedge_with_split_panel_is_genuinely_ambiguous(self, tmp_path, monkeypatch):
        results = tmp_path / "results.json"
        consensus = tmp_path / "consensus.jsonl"
        _write_results(
            results,
            [
                {
                    "id": "f1",
                    "language": "en",
                    "fields": [
                        {"field": "sentiment", "predicted": "mixed", "expected": "negative"},
                    ],
                }
            ],
        )
        _write_consensus(
            consensus,
            [
                {
                    "id": "f1",
                    "mode": "validate",
                    "consensus": {
                        "sentiment": {
                            "silver": None,
                            "agreement": "split",
                            "votes": {"judge_a": "mixed", "judge_b": "negative"},
                        }
                    },
                }
            ],
        )
        monkeypatch.setattr(p2, "RESULTS_PATH", results)
        monkeypatch.setattr(p2, "CONSENSUS_LABELS_PATH", consensus)

        real_hedges, already_correct = p2.classify_abstentions()
        assert already_correct == []
        assert real_hedges[0]["classification"] == "genuinely_ambiguous"

    def test_real_hedge_where_panel_also_hedges_is_genuinely_ambiguous(self, tmp_path, monkeypatch):
        # Regression case found in the real data (Session 7 P2): both judges unanimously
        # land on the hedge value itself -- that must NOT count as "decidable."
        results = tmp_path / "results.json"
        consensus = tmp_path / "consensus.jsonl"
        _write_results(
            results,
            [
                {
                    "id": "f1",
                    "language": "en",
                    "fields": [
                        {"field": "sentiment", "predicted": "mixed", "expected": "positive"},
                    ],
                }
            ],
        )
        _write_consensus(
            consensus,
            [
                {
                    "id": "f1",
                    "mode": "validate",
                    "consensus": {
                        "sentiment": {
                            "silver": "mixed",
                            "agreement": "unanimous",
                            "votes": {"judge_a": "mixed", "judge_b": "mixed"},
                        }
                    },
                }
            ],
        )
        monkeypatch.setattr(p2, "RESULTS_PATH", results)
        monkeypatch.setattr(p2, "CONSENSUS_LABELS_PATH", consensus)

        real_hedges, already_correct = p2.classify_abstentions()
        assert already_correct == []
        assert real_hedges[0]["classification"] == "genuinely_ambiguous"

    def test_non_hedge_fields_are_ignored(self, tmp_path, monkeypatch):
        results = tmp_path / "results.json"
        consensus = tmp_path / "consensus.jsonl"
        _write_results(
            results,
            [
                {
                    "id": "f1",
                    "language": "en",
                    "fields": [
                        {"field": "stars", "predicted": None, "expected": None},
                    ],
                }
            ],
        )
        _write_consensus(consensus, [])
        monkeypatch.setattr(p2, "RESULTS_PATH", results)
        monkeypatch.setattr(p2, "CONSENSUS_LABELS_PATH", consensus)

        real_hedges, already_correct = p2.classify_abstentions()
        assert real_hedges == []
        assert already_correct == []

    def test_committed_prediction_is_not_a_hedge(self, tmp_path, monkeypatch):
        results = tmp_path / "results.json"
        consensus = tmp_path / "consensus.jsonl"
        _write_results(
            results,
            [
                {
                    "id": "f1",
                    "language": "en",
                    "fields": [
                        {"field": "buy_again", "predicted": True, "expected": True},
                    ],
                }
            ],
        )
        _write_consensus(consensus, [])
        monkeypatch.setattr(p2, "RESULTS_PATH", results)
        monkeypatch.setattr(p2, "CONSENSUS_LABELS_PATH", consensus)

        real_hedges, already_correct = p2.classify_abstentions()
        assert real_hedges == []
        assert already_correct == []

    def test_matches_real_repo_data_counts(self):
        # End-to-end sanity check against the actual committed eval/consensus data --
        # locks in the Session 7 P2 numbers cited in
        # docs/specs/wave1-coverage-abstention-analysis.md.
        real_hedges, already_correct = p2.classify_abstentions()

        already_correct_by_field: dict[str, int] = {}
        for field, _language, _fixture_id in already_correct:
            already_correct_by_field[field] = already_correct_by_field.get(field, 0) + 1
        assert already_correct_by_field == {"sentiment": 7, "buy_again": 4}

        by_field_classification: dict[str, dict[str, int]] = {}
        for row in real_hedges:
            field_counts = by_field_classification.setdefault(row["field"], {})
            field_counts[row["classification"]] = field_counts.get(row["classification"], 0) + 1

        assert by_field_classification["buy_again"] == {
            "decidable_but_hedged": 5,
            "genuinely_ambiguous": 5,
        }
        assert by_field_classification["sentiment"] == {"genuinely_ambiguous": 9}
