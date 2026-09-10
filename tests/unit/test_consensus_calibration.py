"""Unit tests for eval/consensus/calibration.py -- pure scoring logic, no live LLM calls."""

from __future__ import annotations

from eval.consensus.calibration import check_item_against_expected, load_control_set


class TestLoadControlSet:
    def test_loads_at_least_ten_items(self):
        control_set = load_control_set()
        assert len(control_set) >= 10

    def test_every_item_has_an_id_text_and_rationale(self):
        for item in load_control_set():
            assert item["id"]
            assert item["text"]
            assert item["why_unambiguous"]
            assert "expected" in item or "expected_list_contains" in item


class TestCheckItemAgainstExpected:
    def test_all_correct_returns_no_misses(self):
        item = {"expected": {"sentiment": "positive", "urgency": "low"}}
        output = {"sentiment": "positive", "urgency": "low"}
        assert check_item_against_expected(item, output) == []

    def test_wrong_field_is_reported_as_a_miss(self):
        item = {"expected": {"sentiment": "positive", "urgency": "low"}}
        output = {"sentiment": "negative", "urgency": "low"}
        assert check_item_against_expected(item, output) == ["sentiment"]

    def test_case_insensitive_string_match(self):
        item = {"expected": {"language": "en"}}
        output = {"language": "EN"}
        assert check_item_against_expected(item, output) == []

    def test_none_output_misses_every_expected_field(self):
        item = {"expected": {"sentiment": "positive", "urgency": "low"}}
        assert set(check_item_against_expected(item, None)) == {"sentiment", "urgency"}

    def test_list_contains_check_passes_on_substring(self):
        item = {"expected_list_contains": {"competitor_mentions": "dyson"}}
        output = {"competitor_mentions": ["Dyson V8"]}
        assert check_item_against_expected(item, output) == []

    def test_list_contains_check_fails_when_absent(self):
        item = {"expected_list_contains": {"competitor_mentions": "dyson"}}
        output = {"competitor_mentions": ["Shark"]}
        assert check_item_against_expected(item, output) == ["competitor_mentions"]

    def test_list_contains_check_fails_on_none_output(self):
        item = {"expected_list_contains": {"feature_requests": "case"}}
        assert check_item_against_expected(item, None) == ["feature_requests"]

    def test_boolean_field_exact_match(self):
        item = {"expected": {"buy_again": True}}
        assert check_item_against_expected(item, {"buy_again": True}) == []
        assert check_item_against_expected(item, {"buy_again": False}) == ["buy_again"]

    def test_numeric_field_exact_match(self):
        item = {"expected": {"stars": 5}}
        assert check_item_against_expected(item, {"stars": 5}) == []
        assert check_item_against_expected(item, {"stars": 4}) == ["stars"]
