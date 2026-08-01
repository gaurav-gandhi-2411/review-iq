"""Unit tests for scripts/measure_redaction_recall.py's scoring/aggregation logic."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.measure_redaction_recall import (  # noqa: E402
    SpanResult,
    aggregate,
    score_fixtures,
)


class TestScoreFixtures:
    def test_redacted_span_is_true_positive(self) -> None:
        fixtures = [
            {"id": 1, "kind": "email", "text": "Email me at a@b.com", "expected_spans": ["a@b.com"]}
        ]
        results = score_fixtures(fixtures)
        assert len(results) == 1
        assert results[0].redacted is True

    def test_survived_span_is_false_negative(self) -> None:
        # "quantum-flux-name" is not a PII pattern the current pipeline recognises,
        # so it will survive redaction -- a deliberate false negative for the test.
        fixtures = [
            {
                "id": 2,
                "kind": "name",
                "text": "Contact quantum-flux-name for details.",
                "expected_spans": ["quantum-flux-name"],
            }
        ]
        results = score_fixtures(fixtures)
        assert results[0].redacted is False

    def test_multiple_spans_scored_independently(self) -> None:
        fixtures = [
            {
                "id": 3,
                "kind": "email",
                "text": "Reach a@b.com or c@d.com",
                "expected_spans": ["a@b.com", "c@d.com"],
            }
        ]
        results = score_fixtures(fixtures)
        assert len(results) == 2
        assert all(r.redacted for r in results)


class TestAggregate:
    def test_per_kind_and_overall_recall(self) -> None:
        results = [
            SpanResult(fixture_id=1, kind="email", span="a@b.com", redacted=True),
            SpanResult(fixture_id=2, kind="email", span="c@d.com", redacted=True),
            SpanResult(fixture_id=3, kind="name", span="Rajesh", redacted=True),
            SpanResult(fixture_id=4, kind="name", span="Priya", redacted=False),
        ]
        summary = aggregate(results)

        assert summary["email"]["recall"] == 1.0
        assert summary["email"]["n"] == 2
        assert summary["name"]["recall"] == 0.5
        assert summary["name"]["tp"] == 1
        assert summary["name"]["fn"] == 1
        assert summary["overall"]["n"] == 4
        assert summary["overall"]["recall"] == 0.75

    def test_ci_widens_with_fewer_samples(self) -> None:
        few = [SpanResult(fixture_id=1, kind="email", span="a@b.com", redacted=True)]
        many = [
            SpanResult(fixture_id=i, kind="email", span=f"{i}@b.com", redacted=True)
            for i in range(50)
        ]
        few_ci = aggregate(few)["overall"]["recall_ci95"]
        many_ci = aggregate(many)["overall"]["recall_ci95"]
        few_width = few_ci[1] - few_ci[0]  # type: ignore[operator]
        many_width = many_ci[1] - many_ci[0]  # type: ignore[operator]
        assert few_width > many_width

    def test_empty_results_no_crash(self) -> None:
        summary = aggregate([])
        assert summary["overall"]["n"] == 0
        assert summary["overall"]["recall"] == 0.0
