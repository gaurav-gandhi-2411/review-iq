"""Google Business Profile reviews connector — interface stub, not yet implemented."""

from __future__ import annotations


class GoogleBusinessSource:
    """Google Business Profile review ingestion source — stub only."""

    @property
    def source_type(self) -> str:
        return "google_business"

    async def fetch_reviews(self) -> list[dict]:
        raise NotImplementedError("Google Business connector is not yet built.")

    def source_meta(self) -> dict[str, object]:
        raise NotImplementedError("Google Business connector is not yet built.")
