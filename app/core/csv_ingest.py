"""Streaming CSV parser for bulk review ingestion."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import UploadFile

MAX_ROWS: int = 500
MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB

_FALLBACK_TEXT_COLS: tuple[str, ...] = ("review_text", "review", "comment", "text")
_CHUNK_SIZE: int = 65536  # 64 KB


class FileTooLargeError(Exception):
    """Raised when upload exceeds MAX_BYTES."""


class RowLimitExceededError(Exception):
    """Raised when CSV has more than MAX_ROWS data rows."""


class CsvColumnError(Exception):
    """Raised when the requested text column is not found."""


async def read_and_validate_csv(
    file: UploadFile,
    text_column: str | None,
    product_column: str | None,
) -> tuple[list[dict[str, str]], str, str | None]:
    """Read a CSV upload in chunks; validate headers and caps.

    Returns:
        (rows, resolved_text_col, resolved_product_col)

        Each row dict contains at least "text" (the review text). No "_original" key
        is added here — callers that need original values should use the raw row dict
        returned alongside.

    Raises:
        FileTooLargeError: file > MAX_BYTES
        RowLimitExceededError: CSV has > MAX_ROWS data rows
        CsvColumnError: text column not found
    """
    # ── 1. Read in chunks, enforce size cap ──────────────────────────────────
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > MAX_BYTES:
            raise FileTooLargeError(f"Upload exceeds {MAX_BYTES // (1024 * 1024)} MB limit")
        chunks.append(chunk)

    content = b"".join(chunks).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    if not reader.fieldnames:
        raise CsvColumnError("CSV has no headers or is empty")

    # ── 2. Resolve text column ────────────────────────────────────────────────
    header_lower: dict[str, str] = {h.strip().lower(): h.strip() for h in reader.fieldnames}

    resolved_text: str | None = None
    if text_column:
        key = text_column.strip().lower()
        if key not in header_lower:
            raise CsvColumnError(
                f"Column '{text_column}' not found. Available: {list(reader.fieldnames)}"
            )
        resolved_text = header_lower[key]
    else:
        for fallback in _FALLBACK_TEXT_COLS:
            if fallback in header_lower:
                resolved_text = header_lower[fallback]
                break

    if resolved_text is None:
        raise CsvColumnError(
            f"No text column found. Tried: {list(_FALLBACK_TEXT_COLS)}. "
            f"Pass ?text_column=<name>. Available: {list(reader.fieldnames)}"
        )

    # ── 3. Resolve product column (optional) ─────────────────────────────────
    resolved_product: str | None = None
    if product_column:
        key = product_column.strip().lower()
        if key in header_lower:
            resolved_product = header_lower[key]

    # ── 4. Stream-parse rows, enforce row cap ─────────────────────────────────
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        if len(rows) >= MAX_ROWS:
            # One more row means we exceed the cap — reject.
            raise RowLimitExceededError(f"Upload exceeds {MAX_ROWS} row limit for free tier")
        text = raw_row.get(resolved_text, "").strip()
        if not text:
            continue  # skip blank rows silently

        row: dict[str, str] = {"text": text}
        if resolved_product and resolved_product in raw_row:
            row["product"] = raw_row[resolved_product].strip()
        rows.append(row)

    return rows, resolved_text, resolved_product
