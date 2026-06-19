from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    """Pluggable review ingestion source.

    Implementors return review rows in a uniform shape:
      {"text": str, "product": str | None, "stars": float | None, "language": str | None}
    Missing keys are allowed; consumers treat absent keys as None.
    """

    @property
    def source_type(self) -> str:
        """Short identifier: 'csv', 'shopify', 'email_forward', etc."""
        ...

    async def fetch_reviews(self) -> list[dict[str, str | float | None]]:
        """Fetch review rows from the source. Raises SourceError on failure."""
        ...

    def source_meta(self) -> dict[str, object]:
        """Arbitrary metadata to store alongside the ingestion job."""
        ...


class SourceError(Exception):
    """Raised by Source.fetch_reviews on retrieval failure."""
