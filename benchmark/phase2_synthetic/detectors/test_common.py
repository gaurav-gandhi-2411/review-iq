"""Unit tests for common.py's extraction-loading/normalization helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from common import load_extractions, to_annotated


def _extraction_record(
    review_id: str = "syn-000001",
    product_id: str = "SYN-BATCH-01",
    error: str | None = None,
) -> dict:
    """Build a minimal extraction record matching the real `extractions.jsonl` schema."""
    extraction = {
        "topics": ["battery", "build_quality"],
        "sentiment": "negative",
        "urgency": "medium",
    }
    if error:
        extraction["_error"] = error
    return {
        "review_id": review_id,
        "product_id": product_id,
        "reviewer_id": "rev-0001",
        "timestamp": "2026-03-09T00:14:24Z",
        "rating": "2",
        "product_category": "electronics",
        "extraction": extraction,
        "latency_ms": 4484,
    }


def test_to_annotated_parses_fields_and_z_suffixed_timestamp() -> None:
    """A trailing 'Z' timestamp parses to an aware UTC datetime, rating coerces to int."""
    annotated = to_annotated(_extraction_record())

    assert annotated.review_id == "syn-000001"
    assert annotated.product_id == "SYN-BATCH-01"
    assert annotated.reviewer_id == "rev-0001"
    assert annotated.timestamp == datetime(2026, 3, 9, 0, 14, 24, tzinfo=UTC)
    assert annotated.rating == 2
    assert annotated.topics == ["battery", "build_quality"]
    assert annotated.sentiment == "negative"
    assert annotated.urgency == "medium"


def test_to_annotated_handles_missing_optional_fields() -> None:
    """Missing/None topics, sentiment, urgency degrade to empty/blank rather than raising."""
    record = _extraction_record()
    record["extraction"]["topics"] = None
    record["extraction"]["sentiment"] = None
    record["extraction"]["urgency"] = None

    annotated = to_annotated(record)

    assert annotated.topics == []
    assert annotated.sentiment == ""
    assert annotated.urgency == ""


def test_load_extractions_skips_error_records(tmp_path: Path) -> None:
    """Records with a truthy extraction._error are skipped; good records are kept."""
    good_1 = _extraction_record(review_id="syn-000001")
    errored = _extraction_record(review_id="syn-000002", error="rate_limited")
    good_2 = _extraction_record(review_id="syn-000003")

    jsonl_path = tmp_path / "extractions.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(r) for r in (good_1, errored, good_2)), encoding="utf-8"
    )

    loaded = load_extractions(jsonl_path)

    assert [r["review_id"] for r in loaded] == ["syn-000001", "syn-000003"]


def test_load_extractions_skips_blank_lines(tmp_path: Path) -> None:
    """Trailing/blank lines in the JSONL file do not raise a JSON decode error."""
    jsonl_path = tmp_path / "extractions.jsonl"
    jsonl_path.write_text(json.dumps(_extraction_record()) + "\n\n", encoding="utf-8")

    loaded = load_extractions(jsonl_path)

    assert len(loaded) == 1
