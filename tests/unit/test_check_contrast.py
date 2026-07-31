"""Unit tests for scripts/check_contrast.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_contrast import (
    ContrastPair,
    KnownFailingPair,
    check_contrast_pairs,
    check_known_failing_pairs,
    contrast_ratio,
    load_tokens,
    relative_luminance,
)


class TestContrastRatioMath:
    def test_black_on_white_is_21_to_1(self) -> None:
        # Hand-computable reference case: pure black vs pure white is the WCAG maximum.
        assert contrast_ratio("#000000", "#FFFFFF") == 21.0

    def test_white_on_black_is_symmetric(self) -> None:
        # Order of the two colours must not matter.
        assert contrast_ratio("#FFFFFF", "#000000") == 21.0

    def test_same_colour_is_1_to_1(self) -> None:
        # Identical foreground/background has zero contrast, ratio floor is 1.0.
        assert contrast_ratio("#808080", "#808080") == 1.0

    def test_relative_luminance_of_white_is_1(self) -> None:
        assert relative_luminance("#FFFFFF") == 1.0

    def test_relative_luminance_of_black_is_0(self) -> None:
        assert relative_luminance("#000000") == 0.0


class TestRealTokensFile:
    def test_current_tokens_json_passes_all_contrast_pairs(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        tokens = load_tokens(repo_root / "design" / "tokens.json")
        contrast_pairs: list[ContrastPair] = tokens["contrastPairs"]  # type: ignore[assignment]
        failures = check_contrast_pairs(contrast_pairs)
        assert failures == []

    def test_current_tokens_json_known_failing_pairs_still_fail(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        tokens = load_tokens(repo_root / "design" / "tokens.json")
        known_failing: list[KnownFailingPair] = tokens["knownFailingPairs"]  # type: ignore[assignment]
        failures = check_known_failing_pairs(known_failing)
        assert failures == []


class TestCatchesARealViolation:
    def test_pair_below_min_ratio_is_flagged(self, tmp_path: Path) -> None:
        fixture = {
            "contrastPairs": [
                # Two near-identical light greys: ratio ~1.05, far below the stated 4.5 floor.
                {"name": "broken-pair", "bg": "#F0F0F0", "fg": "#E8E8E8", "minRatio": 4.5}
            ],
            "knownFailingPairs": [],
        }
        path = tmp_path / "tokens.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")

        tokens = load_tokens(path)
        contrast_pairs: list[ContrastPair] = tokens["contrastPairs"]  # type: ignore[assignment]
        failures = check_contrast_pairs(contrast_pairs)

        assert len(failures) == 1
        assert "broken-pair" in failures[0]
        assert "minRatio" in failures[0]

    def test_stale_known_failing_pair_that_now_passes_is_flagged(self, tmp_path: Path) -> None:
        fixture = {
            "contrastPairs": [],
            "knownFailingPairs": [
                # Black-on-white passes AA easily -- if this were ever documented as a
                # "known failing" pair, the documentation would be stale and must be flagged.
                {
                    "name": "stale-pair",
                    "bg": "#000000",
                    "fg": "#FFFFFF",
                    "ratio": 2.5,
                    "rule": "stale doc",
                },
            ],
        }
        path = tmp_path / "tokens.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")

        tokens = load_tokens(path)
        known_failing: list[KnownFailingPair] = tokens["knownFailingPairs"]  # type: ignore[assignment]
        failures = check_known_failing_pairs(known_failing)

        assert len(failures) >= 1
        assert any("stale-pair" in f for f in failures)

    def test_known_failing_pair_with_drifted_documented_ratio_is_flagged(
        self, tmp_path: Path
    ) -> None:
        fixture = {
            "contrastPairs": [],
            "knownFailingPairs": [
                # Actual ratio for this pair is ~2.84 (see check_contrast.py docstring
                # reference case); documenting it as 1.50 is stale beyond tolerance.
                {
                    "name": "drifted-pair",
                    "bg": "#FF6B35",
                    "fg": "#FFFFFF",
                    "ratio": 1.50,
                    "rule": "drifted doc",
                },
            ],
        }
        path = tmp_path / "tokens.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")

        tokens = load_tokens(path)
        known_failing: list[KnownFailingPair] = tokens["knownFailingPairs"]  # type: ignore[assignment]
        failures = check_known_failing_pairs(known_failing)

        assert len(failures) >= 1
        assert any("drifted-pair" in f for f in failures)
