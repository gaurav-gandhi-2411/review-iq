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


class TestRollingWindow:
    """The docstring promised a rolling window; the code only compared the first 40 chars."""

    REVIEW = "Bass 5/5 Volume 4.5/5 Quality Build 5/5 Best but Boat ko thoda Volume Me sudhar karna"

    def test_mid_text_paste_with_trimmed_opening_is_caught(self):
        pasted = self.REVIEW[30:]  # opening trimmed: the first-40-chars fingerprint misses it
        prompts = {"PROMPTS.md": f"note: {pasted}"}
        assert self.REVIEW[:40] not in prompts["PROMPTS.md"]
        assert len(find_leaks({"f.json": self.REVIEW}, prompts)) == 1

    def test_unrelated_text_sharing_short_phrase_is_clean(self):
        prompts = {"p.py": "Boat ko thoda Volume Me sudhar"}  # 30 chars < window
        assert find_leaks({"f.json": self.REVIEW}, prompts) == []


class TestMainFailsClosed:
    def test_missing_corpus_fails_instead_of_ok(self, tmp_path, monkeypatch):
        import scripts.check_no_heldout_leakage as mod

        monkeypatch.setattr(mod, "HELDOUT_DIR", tmp_path / "absent")
        assert mod.main() == 1

    def test_no_prompt_files_fails_instead_of_ok(self, tmp_path, monkeypatch):
        import scripts.check_no_heldout_leakage as mod

        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.json").write_text(
            '{"review_text": "a sufficiently long held-out review text for the window"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "HELDOUT_DIR", corpus)
        monkeypatch.setattr(mod, "PROMPT_PATHS", (tmp_path / "nothing_here",))
        assert mod.main() == 1

    def test_real_repo_passes_with_only_the_documented_allowlisted_overlap(self):
        import scripts.check_no_heldout_leakage as mod

        assert mod.main() == 0
        assert list(mod.ALLOWLIST) == [("hien-0014.json", "PROMPTS.md")]

    def test_stale_allowlist_entry_fails(self, monkeypatch):
        import scripts.check_no_heldout_leakage as mod

        monkeypatch.setitem(mod.ALLOWLIST, ("hien-0001.json", "PROMPTS.md"), "no overlap")
        assert mod.main() == 1
