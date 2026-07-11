"""Unit tests for app.core.csv_ingest.read_and_validate_csv."""

from __future__ import annotations

import pytest
from app.core.csv_ingest import (
    MAX_BYTES,
    MAX_ROWS,
    CsvColumnError,
    FileTooLargeError,
    RowLimitExceededError,
    neutralize_csv_formula,
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
    rows, resolved_text, resolved_product, resolved_date, date_ambiguous = (
        await read_and_validate_csv(_FakeFile(csv_bytes), None, None)
    )

    assert len(rows) == 3
    assert resolved_text == "review_text"
    assert resolved_product is None
    assert resolved_date is None
    assert date_ambiguous is False
    assert rows[0]["text"] == "Great product"


@pytest.mark.asyncio
async def test_happy_path_explicit_text_column() -> None:
    """CSV with body header; caller passes text_column='body'; works."""
    csv_bytes = b"body,rating\nLove it,5\nHate it,1\n"
    rows, resolved_text, resolved_product, resolved_date, date_ambiguous = (
        await read_and_validate_csv(_FakeFile(csv_bytes), "body", None)
    )

    assert len(rows) == 2
    assert resolved_text == "body"
    assert resolved_product is None
    assert resolved_date is None
    assert date_ambiguous is False
    assert rows[0]["text"] == "Love it"


@pytest.mark.asyncio
async def test_happy_path_product_column() -> None:
    """CSV with review_text and product columns; rows include 'product' key."""
    csv_bytes = b"review_text,product\nAmazing gadget,Widget Pro\nOkay device,Widget Lite\n"
    rows, resolved_text, resolved_product, resolved_date, date_ambiguous = (
        await read_and_validate_csv(_FakeFile(csv_bytes), None, "product")
    )

    assert len(rows) == 2
    assert resolved_text == "review_text"
    assert resolved_product == "product"
    assert resolved_date is None
    assert date_ambiguous is False
    assert rows[0]["product"] == "Widget Pro"
    assert rows[1]["product"] == "Widget Lite"


# ---------------------------------------------------------------------------
# Date-column tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iso_dates_parse_unambiguously() -> None:
    """Year-first ISO dates never need convention detection -- always parse directly."""
    csv_bytes = b"review_text,review_date\nGreat,2026-07-10\nOk,2026-12-01\n"
    rows, _t, _p, resolved_date, date_ambiguous = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None
    )

    assert resolved_date == "review_date"
    assert date_ambiguous is False
    assert rows[0]["review_date"] == "2026-07-10T00:00:00"
    assert rows[1]["review_date"] == "2026-12-01T00:00:00"


@pytest.mark.asyncio
async def test_ambiguous_numeric_dates_resolved_via_self_disambiguating_row() -> None:
    """One row's day>12 (13) disambiguates the whole column's day-first convention, applied to
    every ambiguous row -- never fabricated per-row guessing, one file-wide detected convention."""
    csv_bytes = b"review_text,review_date\nA,13/02/2026\nB,01/03/2026\n"
    rows, _t, _p, _d, date_ambiguous = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None
    )

    assert date_ambiguous is False
    assert rows[0]["review_date"] == "2026-02-13T00:00:00"  # self-resolving: 13 must be the day
    assert rows[1]["review_date"] == "2026-03-01T00:00:00"  # day-first convention applied: 01/03 -> Mar 1


@pytest.mark.asyncio
async def test_ambiguous_numeric_dates_with_no_evidence_never_fabricated() -> None:
    """No row anywhere in the column disambiguates day-vs-month -- the WHOLE column is rejected,
    every row's review_date stays absent. Never guess."""
    csv_bytes = b"review_text,review_date\nA,01/02/2026\nB,03/04/2026\n"
    rows, _t, _p, resolved_date, date_ambiguous = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None
    )

    assert resolved_date == "review_date"
    assert date_ambiguous is True
    assert "review_date" not in rows[0]
    assert "review_date" not in rows[1]


@pytest.mark.asyncio
async def test_date_format_hint_bypasses_ambiguity_detection() -> None:
    """An explicit date_format hint applies directly, skipping the per-file evidence scan --
    same otherwise-ambiguous column as the no-evidence test above, but now resolved."""
    csv_bytes = b"review_text,review_date\nA,01/02/2026\nB,03/04/2026\n"
    rows, _t, _p, _d, date_ambiguous = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None, None, "DMY"
    )

    assert date_ambiguous is False
    assert rows[0]["review_date"] == "2026-02-01T00:00:00"  # day-first: 01/02 -> Feb 1
    assert rows[1]["review_date"] == "2026-04-03T00:00:00"  # day-first: 03/04 -> Apr 3


@pytest.mark.asyncio
async def test_no_date_column_leaves_rows_unaffected() -> None:
    """Backward compatible: omitting date_column (and no fallback match) never adds review_date."""
    csv_bytes = b"review_text\nJust text\n"
    rows, _t, _p, resolved_date, date_ambiguous = await read_and_validate_csv(
        _FakeFile(csv_bytes), None, None
    )

    assert resolved_date is None
    assert date_ambiguous is False
    assert "review_date" not in rows[0]


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_rows_skipped() -> None:
    """A row with empty review_text is silently skipped; returns fewer rows."""
    csv_bytes = b"review_text\nGreat product\n\nAnother review\n"
    rows, resolved_text, resolved_product, _resolved_date, _date_ambiguous = (
        await read_and_validate_csv(_FakeFile(csv_bytes), None, None)
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


# ---------------------------------------------------------------------------
# neutralize_csv_formula — CSV/formula injection defense (CWE-1236)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '=HYPERLINK("http://evil.example/x","click")',
        "=cmd|'/c calc'!A1",
        "+1+1",
        "-2+3",
        "@SUM(A1:A9)",
        "\tsneaky",
        "\rsneaky",
    ],
)
def test_neutralize_csv_formula_prefixes_trigger_chars(raw: str) -> None:
    result = neutralize_csv_formula(raw)
    assert result == "'" + raw
    assert not result.startswith(("=", "+", "-", "@", "\t", "\r"))


@pytest.mark.parametrize(
    "raw",
    [
        "A+B Combo Pack",
        "5-star rated",
        "user@example product",  # @ not leading
        "Widget",
        "",
    ],
)
def test_neutralize_csv_formula_leaves_legitimate_values_untouched(raw: str) -> None:
    assert neutralize_csv_formula(raw) == raw


def test_neutralize_csv_formula_passes_through_non_strings() -> None:
    assert neutralize_csv_formula(None) is None
    assert neutralize_csv_formula(5) == 5
    assert neutralize_csv_formula(True) is True
    assert neutralize_csv_formula(0.9) == 0.9
