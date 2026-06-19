"""Email-forward reviews connector — interface stub, not implemented in v1."""

from __future__ import annotations


class EmailForwardSource:
    """Email-forward review ingestion source — stub only."""

    @property
    def source_type(self) -> str:
        return "email_forward"

    async def fetch_reviews(self) -> list[dict[str, str | float | None]]:
        raise NotImplementedError("Email-forward connector is not yet built.")

    def source_meta(self) -> dict[str, object]:
        raise NotImplementedError("Email-forward connector is not yet built.")
