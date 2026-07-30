"""Unit tests for eval/consensus/candidates.py -- fixture loading + growth-candidate selection.

Uses tmp_path fixtures for the filesystem-reading functions -- no live network, no
dependency on the real eval/fixtures/ or eval/data/flipkart_candidates.jsonl contents.
"""

from __future__ import annotations

import json

from eval.consensus.candidates import (
    existing_text_keys,
    load_existing_fixtures,
    select_growth_candidates,
)


def _write_fixture(path, fixture_id, text, language="en"):
    path.write_text(
        json.dumps(
            {
                "id": fixture_id,
                "review_text": text,
                "ground_truth": {"language": language, "sentiment": "positive"},
            }
        ),
        encoding="utf-8",
    )


class TestLoadExistingFixtures:
    def test_loads_top_level_and_subdir_fixtures(self, tmp_path):
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        (fixtures_dir / "hi-en").mkdir()
        _write_fixture(fixtures_dir / "001_a.json", "001_a", "Great product", "en")
        _write_fixture(fixtures_dir / "hi-en" / "001.json", "hi-en-001", "Accha hai", "hi-en")

        loaded = load_existing_fixtures(fixtures_dir)
        ids = {f["id"] for f in loaded}
        assert ids == {"001_a", "hi-en-001"}

    def test_skips_non_fixture_files(self, tmp_path):
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        (fixtures_dir / "README.md").write_text("not json fixture content", encoding="utf-8")
        (fixtures_dir / "hi-en").mkdir()
        (fixtures_dir / "hi-en" / ".labeling_run.json").write_text(
            json.dumps({"model": "x", "fixtures_written": 15}), encoding="utf-8"
        )
        _write_fixture(fixtures_dir / "001_a.json", "001_a", "Great product", "en")

        loaded = load_existing_fixtures(fixtures_dir)
        assert len(loaded) == 1
        assert loaded[0]["id"] == "001_a"


class TestExistingTextKeys:
    def test_returns_normalized_keys(self, tmp_path):
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        _write_fixture(fixtures_dir / "001_a.json", "001_a", "  Great   Product!  ")
        keys = existing_text_keys(fixtures_dir)
        assert len(keys) == 1
        assert "great" in next(iter(keys))


class TestSelectGrowthCandidates:
    def _candidates(self):
        return [
            {
                "text": f"Review number {i} about this nice gadget purchase",
                "language": "en",
                "char_len": 45,
            }
            for i in range(20)
        ] + [
            {"text": "Bahut accha hai yaar mast product", "language": "hi-en", "char_len": 34},
            {"text": "short", "language": "en", "char_len": 5},  # too short, filtered out
        ]

    def test_filters_by_language_and_char_range(self):
        selected = select_growth_candidates(
            self._candidates(),
            already_used=set(),
            language="en",
            char_range=(20, 100),
            max_count=100,
        )
        assert all(c["language"] == "en" for c in selected)
        assert all(20 <= c["char_len"] <= 100 for c in selected)
        assert len(selected) == 20  # excludes the 5-char one and the hi-en one

    def test_excludes_already_used_texts(self):
        cands = self._candidates()
        used_key = cands[0]["text"].strip().lower()[:100]
        selected = select_growth_candidates(
            cands, already_used={used_key}, language="en", char_range=(20, 100), max_count=100
        )
        assert cands[0]["text"] not in [c["text"] for c in selected]
        assert len(selected) == 19

    def test_caps_at_max_count(self):
        selected = select_growth_candidates(
            self._candidates(), already_used=set(), language="en", char_range=(20, 100), max_count=5
        )
        assert len(selected) == 5

    def test_deterministic_with_fixed_seed(self):
        cands = self._candidates()
        first = select_growth_candidates(
            cands, already_used=set(), language="en", char_range=(20, 100), max_count=5, seed=42
        )
        second = select_growth_candidates(
            cands, already_used=set(), language="en", char_range=(20, 100), max_count=5, seed=42
        )
        assert [c["text"] for c in first] == [c["text"] for c in second]

    def test_assigns_sequential_consensus_ids(self):
        selected = select_growth_candidates(
            self._candidates(), already_used=set(), language="en", char_range=(20, 100), max_count=3
        )
        ids = [c["consensus_id"] for c in selected]
        assert ids == ["grow-en-0001", "grow-en-0002", "grow-en-0003"]
