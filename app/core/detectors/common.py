"""PRODUCTION FORK of benchmark/phase2_synthetic/detectors/common.py.

Only the `AnnotatedReview` dataclass is ported (unchanged) -- the benchmark original's
`load_extractions`/`to_annotated` helpers parse a jsonl file and have no production role.
Production builds `AnnotatedReview` objects from Postgres rows instead, via
app.core.detectors.batch_defect.annotated_reviews_from_rows.

Changes to the benchmark original do not propagate here and vice versa -- if you fix a bug in
one copy, check whether it applies to the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnnotatedReview:
    """One review normalized into the fields detectors need.

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
