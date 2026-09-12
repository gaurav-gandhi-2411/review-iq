"""Unit tests for scripts/check_no_heldout_leakage.py."""

from __future__ import annotations

from scripts.check_no_heldout_leakage import find_leaks


class TestFindLeaks:
    def test_no_overlap_is_clean(self):
        held_out = {"f1.json": "A completely unrelated held-out review about a vacuum cleaner."}
        prompts = {"app/core/prompts/en.py": "SYSTEM_PROMPT = 'Extract fields from a review.'"}
        assert find_leaks(held_out, prompts) == []

    def test_exact_copy_paste_is_caught(self):
        review = "The battery life on this phone is disappointing and it overheats constantly."
        held_out = {"f1.json": review}
        prompts = {"app/core/prompts/en.py": f'FEW_SHOT = "{review}"'}
        leaks = find_leaks(held_out, prompts)
        assert len(leaks) == 1
        assert leaks[0][0] == "f1.json"
        assert leaks[0][1] == "app/core/prompts/en.py"

    def test_short_text_below_threshold_is_ignored(self):
        held_out = {"f1.json": "too short"}
        prompts = {"app/core/prompts/en.py": "too short is in here"}
        assert find_leaks(held_out, prompts) == []

    def test_multiple_fixtures_and_files_all_checked(self):
        held_out = {
            "f1.json": "This is a genuinely long held-out review text about a product defect.",
            "f2.json": "A second, entirely different held-out review about packaging damage issues.",
        }
        prompts = {
            "app/core/prompts/en.py": "no overlap here at all",
            "app/core/prompts/hi.py": "A second, entirely different held-out review about packaging damage issues.",
        }
        leaks = find_leaks(held_out, prompts)
        assert len(leaks) == 1
        assert leaks[0][0] == "f2.json"
        assert leaks[0][1] == "app/core/prompts/hi.py"
