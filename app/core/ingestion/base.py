from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class ReviewRow(TypedDict, total=False):
    """Uniform shape returned by all Source.fetch_reviews() implementations.

    ``text`` is required. All other keys are optional; consumers treat absent
    keys as None.
    """

    text: str  # required — review body
    product: str  # optional product name / title
    stars: float  # optional star rating (1–5)
    language: str  # optional BCP-47 language tag (e.g. "en", "hi")
    source_review_id: str  # optional connector-native ID (dedup key)
    author: str  # optional reviewer name / handle


@runtime_checkable
class Source(Protocol):
    """Pluggable review ingestion source.

    Implementors return review rows in a uniform shape. See ReviewRow.
    Missing keys are allowed; consumers treat absent keys as None.
    """

    @property
    def source_type(self) -> str:
        """Short identifier: 'csv', 'shopify', 'google_business', etc."""
        ...

    async def fetch_reviews(self) -> list[ReviewRow]:
        """Fetch review rows from the source. Raises SourceError on failure."""
        ...

    def source_meta(self) -> dict[str, object]:
        """Arbitrary metadata to store alongside the ingestion job."""
        ...


class SourceError(Exception):
    """Raised by Source.fetch_reviews on retrieval failure."""
