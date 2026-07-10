"""Shared loading/windowing utilities for Phase 2 synthetic-testbed detectors.

Self-contained on purpose: other Phase 2 detectors (e.g. a trend detector) may need similar
`AnnotatedReview` loading/parsing helpers, but this module makes no assumption about their
existence or shape -- import only what you need, do not couple detectors to each other here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnnotatedReview:
    """One review normalized from an `extractions.jsonl` record into the fields detectors need.

    `topics` and `sentiment`/`urgency` come from the production extraction pipeline's output
    (`extraction.topics`, `extraction.sentiment`, `extraction.urgency`), not re-derived from raw
    review text -- detectors consume already-extracted fields, matching the production contract.
    """

    review_id: str
    product_id: str
    reviewer_id: str
    timestamp: datetime
    rating: int
    topics: list[str]
    sentiment: str
    urgency: str


def load_extractions(path: Path) -> list[dict[str, Any]]:
    """Load `extractions.jsonl`, skipping records whose extraction failed.

    A record is skipped when `extraction._error` is set (truthy) -- a failed extraction has no
    reliable `topics`/`sentiment` to detect on, so including it would silently corrupt counts
    rather than just losing one data point.
    """
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("extraction", {}).get("_error"):
                continue
            records.append(record)
    return records


def to_annotated(extraction_record: dict[str, Any]) -> AnnotatedReview:
    """Parse one `extractions.jsonl` record into a normalized `AnnotatedReview`.

    Timestamps are ISO8601 UTC with a trailing `Z` (e.g. `2026-03-09T00:00:00Z`), which
    `datetime.fromisoformat` cannot parse directly on Python < 3.11 -- the `Z`/`+00:00` swap
    keeps this working across the supported version range regardless of the interpreter used to
    run it.
    """
    extraction = extraction_record["extraction"]
    timestamp = datetime.fromisoformat(extraction_record["timestamp"].replace("Z", "+00:00"))
    return AnnotatedReview(
        review_id=extraction_record["review_id"],
        product_id=extraction_record["product_id"],
        reviewer_id=extraction_record["reviewer_id"],
        timestamp=timestamp,
        rating=int(extraction_record["rating"]),
        topics=list(extraction.get("topics") or []),
        sentiment=extraction.get("sentiment") or "",
        urgency=extraction.get("urgency") or "",
    )
