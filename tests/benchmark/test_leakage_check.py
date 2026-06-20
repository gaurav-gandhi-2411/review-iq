from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmark.data.leakage_check import LeakageChecker, _normalize, _sha256

# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_normalize_strips_and_lowercases() -> None:
    assert _normalize("  Hello World  ") == "hello world"


def test_normalize_collapses_internal_whitespace() -> None:
    assert _normalize("foo   bar\t\nbaz") == "foo bar baz"


def test_sha256_deterministic() -> None:
    assert _sha256("test text") == _sha256("test text")
    assert _sha256("test text") != _sha256("other text")


def test_sha256_case_insensitive() -> None:
    assert _sha256("Hello World") == _sha256("hello world")


def test_sha256_whitespace_insensitive() -> None:
    assert _sha256("foo  bar") == _sha256("foo bar")


# ---------------------------------------------------------------------------
# LeakageChecker with a temp fixture dir
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_fixture_dir(tmp_path: Path) -> Path:
    """Create a minimal fixture directory structure with known texts."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    # Standard JSON fixture with review_text field
    (fixtures / "001_test.json").write_text(
        json.dumps({"id": "001", "review_text": "Battery backup is great, loved the product!"}),
        encoding="utf-8",
    )

    # JSONL fixture with text field (authenticity-style)
    auth = tmp_path / "authenticity" / "fixtures"
    auth.mkdir(parents=True)
    (auth / "labeled.jsonl").write_text(
        json.dumps({"id": 1, "text": "Good product good quality good price.", "stars": 5}) + "\n",
        encoding="utf-8",
    )

    # Reply fixture with review_text
    reply = tmp_path / "reply" / "fixtures"
    reply.mkdir(parents=True)
    (reply / "001_reply.json").write_text(
        json.dumps({"id": "r001", "review_text": "Product arrived damaged. Terrible packaging."}),
        encoding="utf-8",
    )

    return tmp_path


def test_from_eval_dir_loads_all_subdirs(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    assert checker.fixture_count == 3  # one from each subdir


def test_is_leaked_exact_match(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    assert checker.is_leaked("Battery backup is great, loved the product!") is not None


def test_is_leaked_case_insensitive(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    assert checker.is_leaked("battery backup is great, loved the product!") is not None


def test_is_leaked_whitespace_insensitive(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    assert checker.is_leaked("  Battery backup  is great,   loved the product!  ") is not None


def test_is_leaked_novel_text_returns_none(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    assert checker.is_leaked("This text was never in any fixture.") is None


def test_check_report_counts(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    candidates = [
        {"id": "c1", "text": "Battery backup is great, loved the product!"},  # LEAKED
        {"id": "c2", "text": "This is a completely fresh review text."},       # clean
        {"id": "c3", "text": "Product arrived damaged. Terrible packaging."},  # LEAKED
    ]
    report = checker.check(candidates)  # type: ignore[arg-type]
    assert report.total_candidates == 3
    assert report.n_leaked == 2
    assert report.n_clean == 1
    assert "c2" in report.clean
    leaked_ids = {e["id"] for e in report.leaked}
    assert "c1" in leaked_ids
    assert "c3" in leaked_ids


def test_check_report_summary_contains_ids(temp_fixture_dir: Path) -> None:
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    candidates = [{"id": "c1", "text": "Battery backup is great, loved the product!"}]
    report = checker.check(candidates)  # type: ignore[arg-type]
    summary = report.summary()
    assert "c1" in summary
    assert "LEAKED" in summary


def test_empty_fixture_dir(tmp_path: Path) -> None:
    """Empty eval dir should produce a checker with 0 fixtures — all candidates clean."""
    checker = LeakageChecker.from_eval_dir(tmp_path)
    assert checker.fixture_count == 0
    candidates = [{"id": "x", "text": "some review"}]
    report = checker.check(candidates)  # type: ignore[arg-type]
    assert report.n_leaked == 0
    assert report.n_clean == 1


def test_missing_text_field_skipped(temp_fixture_dir: Path) -> None:
    """Fixture files with no text/review_text field are silently skipped."""
    fixtures = temp_fixture_dir / "fixtures"
    (fixtures / "no_text.json").write_text(
        json.dumps({"id": "x", "label": "something"}), encoding="utf-8"
    )
    checker = LeakageChecker.from_eval_dir(temp_fixture_dir)
    # Still only 3 from the original setup (no_text.json adds nothing)
    assert checker.fixture_count == 3
