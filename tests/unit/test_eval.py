"""Unit tests for eval scoring functions — no LLM calls."""

from __future__ import annotations

import pytest
from eval.runner import (
    FixtureResult,
    _exact_score,
    _fuzzy_list_score,
    _set_f1,
    _tolerance_score,
    aggregate_score,
    per_language_scores,
    score_fixture,
)


class TestExactScore:
    def test_both_none(self):
        assert _exact_score(None, None) == 1.0

    def test_predicted_none(self):
        assert _exact_score(None, "positive") == 0.0

    def test_expected_none(self):
        assert _exact_score("positive", None) == 0.0

    def test_string_match_case_insensitive(self):
        assert _exact_score("Positive", "positive") == 1.0

    def test_string_mismatch(self):
        assert _exact_score("negative", "positive") == 0.0

    def test_bool_match(self):
        assert _exact_score(True, True) == 1.0

    def test_bool_mismatch(self):
        assert _exact_score(False, True) == 0.0

    def test_int_match(self):
        assert _exact_score(5, 5) == 1.0

    def test_int_mismatch(self):
        assert _exact_score(3, 5) == 0.0

    def test_bool_none(self):
        assert _exact_score(None, True) == 0.0


class TestSetF1:
    def test_both_empty(self):
        assert _set_f1([], []) == 1.0

    def test_predicted_empty(self):
        assert _set_f1([], ["battery"]) == 0.0

    def test_expected_empty(self):
        assert _set_f1(["battery"], []) == 0.0

    def test_perfect_match(self):
        assert _set_f1(["battery", "noise"], ["battery", "noise"]) == 1.0

    def test_case_insensitive(self):
        assert _set_f1(["Battery"], ["battery"]) == 1.0

    def test_partial_overlap(self):
        score = _set_f1(["battery", "noise", "price"], ["battery", "noise"])
        assert 0.0 < score < 1.0

    def test_no_overlap(self):
        assert _set_f1(["battery"], ["noise"]) == 0.0

    def test_whitespace_stripped(self):
        assert _set_f1([" battery "], ["battery"]) == 1.0


class TestFuzzyListScore:
    def test_both_empty(self):
        assert _fuzzy_list_score([], []) == 1.0

    def test_predicted_empty(self):
        assert _fuzzy_list_score([], ["poor battery life"]) == 0.0

    def test_expected_empty(self):
        assert _fuzzy_list_score(["good suction"], []) == 0.0

    def test_exact_phrases(self):
        score = _fuzzy_list_score(["incredible suction"], ["incredible suction"])
        assert score == 1.0

    def test_partial_token_overlap(self):
        score = _fuzzy_list_score(
            ["great suction power"],
            ["amazing suction"],
        )
        assert score > 0.0

    def test_short_words_excluded(self):
        # "of" and "is" are filtered (len <= 2)
        score = _fuzzy_list_score(["the of is"], ["the of is"])
        assert score == 1.0  # all empty → both empty → 1.0

    def test_no_token_overlap(self):
        assert _fuzzy_list_score(["excellent suction"], ["poor battery"]) == 0.0


class TestToleranceScore:
    def test_both_none(self):
        assert _tolerance_score(None, None, 1) == 1.0

    def test_predicted_none(self):
        assert _tolerance_score(None, 3, 1) == 0.0

    def test_expected_none(self):
        assert _tolerance_score(3, None, 1) == 0.0

    def test_exact_match(self):
        assert _tolerance_score(3, 3, 0) == 1.0

    def test_within_tolerance(self):
        assert _tolerance_score(4, 3, 1) == 1.0
        assert _tolerance_score(2, 3, 1) == 1.0

    def test_outside_tolerance(self):
        assert _tolerance_score(5, 3, 1) == 0.0
        assert _tolerance_score(1, 3, 1) == 0.0

    def test_zero_tolerance_miss(self):
        assert _tolerance_score(4, 3, 0) == 0.0


class TestScoreFixture:
    @pytest.fixture
    def simple_fixture(self):
        return {
            "ground_truth": {
                "sentiment": "positive",
                "stars": None,
                "topics": ["battery", "noise"],
                "pros": ["great suction", "quiet"],
                "stars_inferred": 4,
            },
            "scoring_notes": {
                "exact_match_fields": ["sentiment", "stars"],
                "set_overlap_fields": ["topics"],
                "fuzzy_fields": ["pros"],
                "tolerance_fields": {"stars_inferred": 1},
            },
        }

    def test_perfect_extraction(self, simple_fixture):
        extraction = {
            "sentiment": "positive",
            "stars": None,
            "topics": ["battery", "noise"],
            "pros": ["great suction", "quiet"],
            "stars_inferred": 4,
        }
        results = score_fixture(simple_fixture, extraction)
        assert all(fr.score == 1.0 for fr in results)

    def test_wrong_sentiment(self, simple_fixture):
        extraction = {
            "sentiment": "negative",
            "stars": None,
            "topics": ["battery", "noise"],
            "pros": ["great suction", "quiet"],
            "stars_inferred": 4,
        }
        results = score_fixture(simple_fixture, extraction)
        sentiment_result = next(r for r in results if r.field == "sentiment")
        assert sentiment_result.score == 0.0

    def test_tolerance_field_within_range(self, simple_fixture):
        extraction = {
            "sentiment": "positive",
            "stars": None,
            "topics": ["battery", "noise"],
            "pros": ["great suction", "quiet"],
            "stars_inferred": 5,  # off by 1, tolerance is 1
        }
        results = score_fixture(simple_fixture, extraction)
        tol_result = next(r for r in results if r.field == "stars_inferred")
        assert tol_result.score == 1.0

    def test_empty_topics_both_empty(self, simple_fixture):
        simple_fixture["ground_truth"]["topics"] = []
        extraction = {
            "sentiment": "positive",
            "stars": None,
            "topics": [],
            "pros": ["great suction", "quiet"],
            "stars_inferred": 4,
        }
        results = score_fixture(simple_fixture, extraction)
        topic_result = next(r for r in results if r.field == "topics")
        assert topic_result.score == 1.0

    def test_field_count(self, simple_fixture):
        extraction = {
            "sentiment": "positive",
            "stars": None,
            "topics": [],
            "pros": [],
            "stars_inferred": 4,
        }
        results = score_fixture(simple_fixture, extraction)
        # 2 exact + 1 set + 1 fuzzy + 1 tolerance = 5
        assert len(results) == 5


class TestAggregateScore:
    def test_empty_list(self):
        assert aggregate_score([]) == 0.0

    def test_all_perfect(self):
        results = [FixtureResult("a", overall_score=1.0), FixtureResult("b", overall_score=1.0)]
        assert aggregate_score(results) == 1.0

    def test_all_zero(self):
        results = [FixtureResult("a", overall_score=0.0), FixtureResult("b", overall_score=0.0)]
        assert aggregate_score(results) == 0.0

    def test_mixed(self):
        results = [FixtureResult("a", overall_score=1.0), FixtureResult("b", overall_score=0.0)]
        assert aggregate_score(results) == pytest.approx(0.5)


class TestPerLanguageScores:
    def test_single_language(self):
        results = [FixtureResult("a", overall_score=0.8), FixtureResult("b", overall_score=1.0)]
        lang_map = {"a": "en", "b": "en"}
        scores = per_language_scores(results, lang_map)
        assert scores == {"en": pytest.approx(0.9)}

    def test_multiple_languages(self):
        results = [
            FixtureResult("a", overall_score=1.0),
            FixtureResult("b", overall_score=0.6),
            FixtureResult("c", overall_score=0.8),
        ]
        lang_map = {"a": "en", "b": "hi-en", "c": "hi"}
        scores = per_language_scores(results, lang_map)
        assert scores["en"] == pytest.approx(1.0)
        assert scores["hi-en"] == pytest.approx(0.6)
        assert scores["hi"] == pytest.approx(0.8)

    def test_missing_fixture_defaults_to_en(self):
        results = [FixtureResult("unknown", overall_score=0.9)]
        scores = per_language_scores(results, {})
        assert scores == {"en": pytest.approx(0.9)}

    def test_empty_results(self):
        scores = per_language_scores([], {})
        assert scores == {}
