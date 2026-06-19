"""Unit tests for app.core.ingestion — Source protocol and CSVSource adapter."""

from __future__ import annotations

import pytest
from app.core.ingestion.base import Source, SourceError
from app.core.ingestion.csv_source import CSVSource
from app.core.ingestion.email_source import EmailForwardSource
from app.core.ingestion.shopify_source import ShopifySource


class _FakeUploadFile:
    """Minimal UploadFile stand-in that drains bytes in arbitrarily-sized chunks."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._content[self._pos :]
            self._pos = len(self._content)
        else:
            chunk = self._content[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk


# ---------------------------------------------------------------------------
# Protocol structural check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_protocol_structural_check() -> None:
    """CSVSource satisfies the Source Protocol at runtime via isinstance."""
    csv_bytes = b"review_text,product_name\nGreat product,Widget A\n"
    fake_file = _FakeUploadFile(csv_bytes)
    source = CSVSource(fake_file, text_column="review_text", product_column="product_name")
    assert isinstance(source, Source)


# ---------------------------------------------------------------------------
# Happy-path round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_source_fetch_reviews_happy_path() -> None:
    """fetch_reviews returns the expected row dict; source_meta reflects resolved columns."""
    csv_bytes = b'review_text,product_name\n"Great product","Widget A"\n'
    fake_file = _FakeUploadFile(csv_bytes)
    source = CSVSource(fake_file, text_column="review_text", product_column="product_name")

    rows = await source.fetch_reviews()

    assert rows == [{"text": "Great product", "product": "Widget A"}]
    assert source.source_meta() == {
        "text_column": "review_text",
        "product_column": "product_name",
    }


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_source_file_too_large_raises_source_error() -> None:
    """An oversized upload is wrapped as SourceError (not FileTooLargeError)."""
    oversized = b"x" * (5 * 1024 * 1024 + 1)
    fake_file = _FakeUploadFile(oversized)
    source = CSVSource(fake_file)

    with pytest.raises(SourceError):
        await source.fetch_reviews()


# ---------------------------------------------------------------------------
# Stub sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shopify_source_raises_not_implemented() -> None:
    """ShopifySource.fetch_reviews raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        await ShopifySource().fetch_reviews()


@pytest.mark.asyncio
async def test_email_source_raises_not_implemented() -> None:
    """EmailForwardSource.fetch_reviews raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        await EmailForwardSource().fetch_reviews()
