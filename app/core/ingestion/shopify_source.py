"""Shopify reviews connector — interface stub, not implemented in v1."""

from __future__ import annotations


class ShopifySource:
    """Shopify review ingestion source — stub only."""

    @property
    def source_type(self) -> str:
        return "shopify"

    async def fetch_reviews(self) -> list[dict[str, str | float | None]]:
        raise NotImplementedError("Shopify connector is not yet built.")

    def source_meta(self) -> dict[str, object]:
        raise NotImplementedError("Shopify connector is not yet built.")
