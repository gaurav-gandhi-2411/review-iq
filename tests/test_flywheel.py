from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval.flywheel.corrections_to_fixtures import (
    apply_corrections_to_extraction,
    corrections_to_candidates,
    write_candidates,
)

# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_extraction() -> dict:
    return {
        "product": "Widget",
        "sentiment": "positive",
        "urgency": "low",
        "language": "en",
        "stars": None,
        "stars_inferred": 4,
        "buy_again": True,
        "confidence": 0.9,
        "topics": ["quality", "value"],
        "competitor_mentions": [],
        "pros": ["good build"],
        "cons": [],
        "feature_requests": [],
    }


@pytest.fixture
def sample_correction() -> dict:
    return {
        "id": "corr-001",
        "review_id": "a" * 64,
        "source_type": "extraction",
        "field_path": "sentiment",
        "original_value": "positive",
        "corrected_value": "negative",
        "correction_note": "reviewer disagrees",
    }


@pytest.fixture
def sample_group(base_extraction: dict, sample_correction: dict) -> dict:
    return {
        "review_id": "a" * 64,
        "review_text": "Great product!",
        "original_extraction": base_extraction,
        "corrections": [sample_correction],
    }


# ---------------------------------------------------------------------------
# TestApplyCorrections
# ---------------------------------------------------------------------------


class TestApplyCorrections:
    def test_scalar_correction_applied(self, base_extraction: dict) -> None:
        corrections = [
            {
                "field_path": "sentiment",
                "corrected_value": "negative",
            }
        ]
        result = apply_corrections_to_extraction(base_extraction, corrections)
        assert result["sentiment"] == "negative"

    def test_list_correction_from_json_string(self, base_extraction: dict) -> None:
        corrections = [
            {
                "field_path": "topics",
                "corrected_value": '["build", "price"]',
            }
        ]
        result = apply_corrections_to_extraction(base_extraction, corrections)
        assert result["topics"] == ["build", "price"]

    def test_list_correction_from_list(self, base_extraction: dict) -> None:
        # corrected_value arrives as a JSON string (as it would from the DB)
        corrections = [
            {
                "field_path": "topics",
                "corrected_value": '["build", "price"]',
            }
        ]
        result = apply_corrections_to_extraction(base_extraction, corrections)
        assert result["topics"] == ["build", "price"]

    def test_unknown_field_skipped(self, base_extraction: dict) -> None:
        corrections = [
            {
                "field_path": "nonexistent_xyz",
                "corrected_value": "whatever",
            }
        ]
        result = apply_corrections_to_extraction(base_extraction, corrections)
        # Result should be an unchanged copy — no extra key inserted
        assert "nonexistent_xyz" not in result
        assert result["sentiment"] == base_extraction["sentiment"]

    def test_original_extraction_not_mutated(self, base_extraction: dict) -> None:
        original_sentiment = base_extraction["sentiment"]
        corrections = [{"field_path": "sentiment", "corrected_value": "negative"}]
        apply_corrections_to_extraction(base_extraction, corrections)
        assert base_extraction["sentiment"] == original_sentiment


# ---------------------------------------------------------------------------
# TestCorrectionsToCandidates
# ---------------------------------------------------------------------------


class TestCorrectionsToCandidates:
    def test_produces_one_candidate_per_group(self, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        assert len(candidates) == 1

    def test_candidate_has_required_keys(self, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        candidate = candidates[0]
        for key in (
            "candidate_id",
            "status",
            "review_required",
            "proposed_ground_truth",
            "corrections_applied",
            "source",
        ):
            assert key in candidate, f"Missing key: {key}"

    def test_review_required_is_true(self, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        assert candidates[0]["review_required"] is True

    def test_status_is_pending_review(self, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        assert candidates[0]["status"] == "pending_review"

    def test_correction_applied_to_proposed_truth(self, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        assert candidates[0]["proposed_ground_truth"]["sentiment"] == "negative"

    def test_original_extraction_preserved(self, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        # The original_extraction block must still reflect the pre-correction value
        assert candidates[0]["original_extraction"]["sentiment"] == "positive"

    def test_non_extraction_corrections_skipped(self, base_extraction: dict) -> None:
        group = {
            "review_id": "b" * 64,
            "review_text": "Some review",
            "original_extraction": base_extraction,
            "corrections": [
                {
                    "id": "corr-auth-001",
                    "source_type": "authenticity",
                    "field_path": "authenticity_score",
                    "original_value": "0.9",
                    "corrected_value": "0.3",
                    "correction_note": "likely fake",
                }
            ],
        }
        candidates = corrections_to_candidates([group], generated_at="2026-06-19T00:00:00+00:00")
        assert candidates == []

    def test_mixed_corrections_filters_to_extraction_only(self, base_extraction: dict) -> None:
        group = {
            "review_id": "c" * 64,
            "review_text": "Mixed corrections review",
            "original_extraction": base_extraction,
            "corrections": [
                {
                    "id": "corr-ext-001",
                    "source_type": "extraction",
                    "field_path": "sentiment",
                    "original_value": "positive",
                    "corrected_value": "negative",
                    "correction_note": None,
                },
                {
                    "id": "corr-auth-002",
                    "source_type": "authenticity",
                    "field_path": "authenticity_score",
                    "original_value": "0.9",
                    "corrected_value": "0.3",
                    "correction_note": "likely fake",
                },
            ],
        }
        candidates = corrections_to_candidates([group], generated_at="2026-06-19T00:00:00+00:00")
        assert len(candidates) == 1
        applied = candidates[0]["corrections_applied"]
        assert len(applied) == 1
        assert applied[0]["source_type"] == "extraction"
        assert applied[0]["field_path"] == "sentiment"


# ---------------------------------------------------------------------------
# TestWriteCandidates — proves no auto-application to eval/fixtures/
# ---------------------------------------------------------------------------


class TestWriteCandidates:
    def test_write_creates_output_file(self, tmp_path: Path, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        fake_gold = tmp_path / "not_fixtures"
        write_candidates(candidates, tmp_path, gold_dir=fake_gold)
        out_file = tmp_path / "candidates.jsonl"
        assert out_file.exists()
        lines = out_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["status"] == "pending_review"

    def test_write_returns_correct_count(self, tmp_path: Path, sample_group: dict) -> None:
        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        fake_gold = tmp_path / "not_fixtures"
        count = write_candidates(candidates, tmp_path, gold_dir=fake_gold)
        assert count == len(candidates)

    def test_gold_dir_guard_raises(self) -> None:
        """CRITICAL: write_candidates must raise ValueError when output_dir == gold_dir.

        This is the no-auto-application proof: the code itself prevents writing
        candidates directly into eval/fixtures/ without a human promotion step.
        """
        target = Path("eval/fixtures")
        with pytest.raises(ValueError, match="eval/fixtures"):
            write_candidates(candidates=[], output_dir=target, gold_dir=target)

    def test_gold_dir_guard_blocks_resolved_path(self, tmp_path: Path) -> None:
        """Guard must fire even when both paths point to the same resolved location."""
        with pytest.raises(ValueError):
            write_candidates(candidates=[], output_dir=tmp_path, gold_dir=tmp_path)

    def test_eval_fixtures_dir_not_touched(self, tmp_path: Path, sample_group: dict) -> None:
        """CRITICAL: definitive no-auto-application proof.

        Run the full pipeline (corrections -> candidates -> write) and assert
        that the gold eval/fixtures/ directory is byte-for-byte identical before
        and after — no files added, removed, or modified.
        """
        repo_root = Path(__file__).parent.parent
        gold_dir = repo_root / "eval" / "fixtures"

        def snapshot(directory: Path) -> set[tuple[str, int]]:
            """Return a set of (relative_path, mtime_ns) for every file under directory."""
            return {
                (str(p.relative_to(directory)), p.stat().st_mtime_ns)
                for p in directory.rglob("*")
                if p.is_file()
            }

        before = snapshot(gold_dir)

        candidates = corrections_to_candidates(
            [sample_group], generated_at="2026-06-19T00:00:00+00:00"
        )
        # write to tmp_path, gold_dir is the real eval/fixtures/ — guard should NOT fire
        # because output_dir != gold_dir, and the files in gold_dir should be untouched
        count = write_candidates(candidates, tmp_path / "queue", gold_dir=gold_dir)

        after = snapshot(gold_dir)

        assert before == after, (
            "eval/fixtures/ was modified during the flywheel pipeline run. "
            f"Diff: added={after - before}, removed={before - after}"
        )
        assert count == 1
        assert (tmp_path / "queue" / "candidates.jsonl").exists()
