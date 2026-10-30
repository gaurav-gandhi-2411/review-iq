"""Unit tests for the pure helpers in scripts/analyze_pros_gold.py (no artifacts, no model)."""

from __future__ import annotations

import pytest
from scripts import analyze_pros_gold as a


def test_both_empty_is_perfect_and_one_empty_is_zero():
    assert a.soft_f1([], [], 0.5) == 1.0
    assert a.soft_f1(["good sound"], [], 0.5) == 0.0


def test_paraphrase_matches_only_below_its_jaccard():
    # "battery is good" vs "good battery": token Jaccard 2/3.
    assert a.soft_f1(["battery is good"], ["good battery"], 0.6) == pytest.approx(1.0)
    assert a.soft_f1(["battery is good"], ["good battery"], 0.8) == 0.0


def test_matching_is_one_to_one():
    # Two predictions cannot both claim the single gold item.
    assert a.soft_f1(["good sound", "good sound quality"], ["good sound"], 0.5) == pytest.approx(
        2 * 0.5 * 1.0 / 1.5
    )


def test_snake_case_topics_split_into_tokens():
    assert a.soft_f1(["sound_quality"], ["sound"], 0.5, canon=lambda x: x) == pytest.approx(1.0)


def test_score_is_monotone_non_increasing_in_threshold():
    pred, gold = ["great bass", "good for all kinds of music"], ["great bass", "good for music"]
    scores = [a.soft_f1(pred, gold, t) for t in a.ITEM_THRESHOLDS]
    assert scores == sorted(scores, reverse=True)
