"""Unit tests for app.core.csv_ingest.read_and_validate_csv."""

from __future__ import annotations

import pytest
from app.core.csv_ingest import (
    MAX_BYTES,
    MAX_ROWS,
    CsvColumnError,
    FileTooLargeError,
    RowLimitExceededError,
    read_and_validate_csv,
)


class _FakeFile:
    """Minimal UploadFile mock that drains bytes in 64KB chunks."""

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
# Happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_review_text_column() -> None:
    """CSV with review_text header is auto-detected; returns 3 rows."""
    csv_bytes = b"review_text\nGreat product\nDecent item\nPoor quality\n"
    rows, resolved_text, resolved_product = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None
    )

    assert len(rows) == 3
    assert resolved_text == "review_text"
    assert resolved_product is None
    assert rows[0]["text"] == "Great product"


@pytest.mark.asyncio
async def test_happy_path_explicit_text_column() -> None:
    """CSV with body header; caller passes text_column='body'; works."""
    csv_bytes = b"body,rating\nLove it,5\nHate it,1\n"
    rows, resolved_text, resolved_product = await read_and_validate_csv(
        _FakeFile(csv_bytes), "body", None
    )

    assert len(rows) == 2
    assert resolved_text == "body"
    assert resolved_product is None
    assert rows[0]["text"] == "Love it"


@pytest.mark.asyncio
async def test_happy_path_product_column() -> None:
    """CSV with review_text and product columns; rows include 'product' key."""
    csv_bytes = b"review_text,product\nAmazing gadget,Widget Pro\nOkay device,Widget Lite\n"
    rows, resolved_text, resolved_product = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, "product"
    )

    assert len(rows) == 2
    assert resolved_text == "review_text"
    assert resolved_product == "product"
    assert rows[0]["product"] == "Widget Pro"
    assert rows[1]["product"] == "Widget Lite"


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_rows_skipped() -> None:
    """A row with empty review_text is silently skipped; returns fewer rows."""
    csv_bytes = b"review_text\nGreat product\n\nAnother review\n"
    rows, resolved_text, resolved_product = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None
    )

    # The empty row should have been skipped
    assert len(rows) == 2
    texts = [r["text"] for r in rows]
    assert "Great product" in texts
    assert "Another review" in texts


# ---------------------------------------------------------------------------
# Error / limit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_too_large_raises() -> None:
    """Content > 5 MB raises FileTooLargeError."""
    # Create content that is slightly over the 5 MB limit
    oversized = b"x" * (MAX_BYTES + 1)
    with pytest.raises(FileTooLargeError):
        await read_and_validate_csv(_FakeFile(oversized), None, None)


@pytest.mark.asyncio
async def test_row_limit_exceeded_raises() -> None:
    """501 data rows raises RowLimitExceededError."""
    num_rows = MAX_ROWS + 1  # 501
    lines = ["review_text"] + [f"Review number {i}" for i in range(num_rows)]
    csv_bytes = "\n".join(lines).encode()
    with pytest.raises(RowLimitExceededError):
        await read_and_validate_csv(_FakeFile(csv_bytes), None, None)


@pytest.mark.asyncio
async def test_missing_custom_text_column_raises() -> None:
    """CSV has review_text but caller specifies text_column='nonexistent'; raises CsvColumnError."""
    csv_bytes = b"review_text\nGreat product\n"
    with pytest.raises(CsvColumnError):
        await read_and_validate_csv(_FakeFile(csv_bytes), "nonexistent", None)


@pytest.mark.asyncio
async def test_no_fallback_text_column_raises() -> None:
    """CSV with only date,rating headers (no fallback match); raises CsvColumnError."""
    csv_bytes = b"date,rating\n2024-01-01,5\n2024-01-02,3\n"
    with pytest.raises(CsvColumnError):
        await read_and_validate_csv(_FakeFile(csv_bytes), None, None)


@pytest.mark.asyncio
async def test_empty_csv_raises() -> None:
    """Empty bytes (no headers) raises CsvColumnError."""
    with pytest.raises(CsvColumnError):
        await read_and_validate_csv(_FakeFile(b""), None, None)
