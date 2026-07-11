"""Streaming CSV parser for bulk review ingestion."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import TYPE_CHECKING

from dateutil import parser as dateutil_parser
from dateutil.parser import ParserError

if TYPE_CHECKING:
    from fastapi import UploadFile

MAX_ROWS: int = 500
MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB

_FALLBACK_TEXT_COLS: tuple[str, ...] = ("review_text", "review", "comment", "text")
_FALLBACK_DATE_COLS: tuple[str, ...] = ("review_date", "date", "created_at", "review_created_at")
_CHUNK_SIZE: int = 65536  # 64 KB

# Year-first (ISO 8601 convention): YYYY-MM-DD or YYYY/MM/DD, optional time suffix. Unambiguous by
# construction -- month always follows the year, day always follows the month. Must be special-
# cased: dateutil's `dayfirst` flag INCORRECTLY swaps month/day even for year-first strings
# (verified empirically: `parser.parse("2026-07-10", dayfirst=True)` wrongly yields Oct 7 instead
# of Jul 10) -- dayfirst is a hint for which-side-is-day, and it misapplies even when the year's
# position already settles that question.
_YEAR_FIRST_RE = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T].*)?\s*$")

# Genuinely ambiguous shape: D/M/Y or M/D/Y with a year-last, both leading numbers 1-2 digits.
# Optional trailing time. This is the ONLY shape that needs a resolved file-wide convention.
_AMBIGUOUS_NUMERIC_RE = re.compile(
    r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$"
)


class FileTooLargeError(Exception):
    """Raised when upload exceeds MAX_BYTES."""


class RowLimitExceededError(Exception):
    """Raised when CSV has more than MAX_ROWS data rows."""


class CsvColumnError(Exception):
    """Raised when the requested text column is not found."""


def _parse_year_first(value: str) -> datetime | None:
    """Parse a year-first date (ISO 8601 convention) -- always unambiguous, dayfirst never
    applies. Returns None if `value` isn't year-first shaped or fails to parse."""
    if not _YEAR_FIRST_RE.match(value):
        return None
    try:
        return dateutil_parser.parse(value, dayfirst=False)
    except (ValueError, OverflowError, ParserError):
        return None


def _ambiguous_numeric_groups(value: str) -> tuple[int, int] | None:
    """If `value` matches the ambiguous D/M/Y (year-last) numeric shape, return its two leading
    numeric positions as (pos1, pos2) -- which one is day is exactly the ambiguity. None if the
    value doesn't match this shape at all (e.g. year-first, or a textual/month-name date)."""
    m = _AMBIGUOUS_NUMERIC_RE.match(value)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def detect_dayfirst_convention(values: list[str]) -> bool | None:
    """Scan every ambiguous (year-last, numeric D/M/Y) value in a date column for SELF-RESOLVING
    evidence -- a value where one position is >12 and therefore can only be the day, regardless of
    which position it's in (dateutil itself resolves these consistently either way; see
    test_csv_ingest.py for the empirical basis). Collecting one vote per self-resolving value:

    Returns:
        True  -- day-first (DD/MM/YYYY) convention, evidenced and consistent across the column.
        False -- month-first (MM/DD/YYYY) convention, evidenced and consistent across the column.
        None  -- no self-resolving evidence anywhere in the column, OR the evidence contradicts
                 itself (some rows imply day-first, others month-first -- a mixed/malformed
                 column). Both cases mean "cannot safely determine the convention" and must be
                 treated identically: never guess.
    """
    votes: set[bool] = set()
    for value in values:
        groups = _ambiguous_numeric_groups(value.strip())
        if groups is None:
            continue
        pos1, pos2 = groups
        if pos1 > 12 and pos2 <= 12:
            votes.add(True)  # first position can only be day -> day-first
        elif pos2 > 12 and pos1 <= 12:
            votes.add(False)  # second position can only be day -> month-first
        # both > 12 (invalid date, skip) or both <= 12 (no evidence from this value either way)
    return votes.pop() if len(votes) == 1 else None


def parse_review_date(value: str, dayfirst: bool | None) -> datetime | None:
    """Parse one review-date CSV value. Never fabricates: returns None rather than guessing.

    - Year-first (ISO-style) values are always unambiguous -- parsed directly.
    - Ambiguous numeric D/M/Y (year-last) values: if the value is itself self-resolving (one
      position > 12), it parses correctly regardless of the file-wide `dayfirst` convention. If
      genuinely ambiguous (both positions <= 12) and no file-wide convention was resolved
      (`dayfirst is None`), returns None -- never guess.
    - Anything else (month-name dates like "10 Jul 2026", or unparseable garbage) is parsed
      directly; `dayfirst` is irrelevant for these (verified empirically unambiguous regardless
      of the flag) or parsing simply fails.
    """
    value = value.strip()
    if not value:
        return None

    parsed = _parse_year_first(value)
    if parsed is not None:
        return parsed

    groups = _ambiguous_numeric_groups(value)
    if groups is not None:
        pos1, pos2 = groups
        if pos1 > 12 and pos2 <= 12:
            effective_dayfirst = True
        elif pos2 > 12 and pos1 <= 12:
            effective_dayfirst = False
        elif dayfirst is not None:
            effective_dayfirst = dayfirst
        else:
            return None  # genuinely ambiguous, no resolved file-wide convention -- never guess
        try:
            return dateutil_parser.parse(value, dayfirst=effective_dayfirst)
        except (ValueError, OverflowError, ParserError):
            return None

    try:
        return dateutil_parser.parse(value)
    except (ValueError, OverflowError, ParserError):
        return None


# Leading characters that Excel/Sheets/LibreOffice interpret as a formula/DDE trigger
# when a cell is opened (CWE-1236). Tab and CR are included alongside the more familiar
# = + - @ per standard CSV-injection guidance -- a value starting with either can also
# be misparsed as a formula prefix by some spreadsheet importers.
_CSV_FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_formula(value: object) -> object:
    """Defuse formula/DDE injection in a value about to be written to an exported CSV cell.

    Only the FIRST character matters to spreadsheet formula detection -- a value like
    "A+B" or "5-star" is untouched (the trigger character isn't leading), only a value
    that STARTS with one of the trigger characters gets a leading `'` prefix, which
    Excel/Sheets/LibreOffice render as a literal apostrophe-quoted string, not a formula.
    Non-string values pass through unchanged.
    """
    if not isinstance(value, str) or not value:
        return value
    if value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


async def read_and_validate_csv(
    file: UploadFile,
    text_column: str | None,
    product_column: str | None,
    date_column: str | None = None,
    date_format: str | None = None,
) -> tuple[list[dict[str, str]], str, str | None, str | None, bool]:
    """Read a CSV upload in chunks; validate headers and caps.

    Args:
        date_column: optional column name holding the review's ORIGINAL post date. Auto-detected
            via `_FALLBACK_DATE_COLS` if not given (same pattern as text_column's fallback).
        date_format: optional explicit hint, "DMY" or "MDY" -- when given, skips the per-file
            ambiguity detection entirely for ambiguous numeric dates. Opt-in only; unrecognized
            values are ignored (falls back to auto-detection), never raises.

    Returns:
        (rows, resolved_text_col, resolved_product_col, resolved_date_col, date_ambiguous)

        Each row dict contains "text" (required), optionally "product", optionally "review_date"
        (ISO8601 string -- absent, never fabricated, if that row's date didn't parse).
        `date_ambiguous` is True iff a date column was resolved but its convention could not be
        determined and evidence was self-contradictory or entirely absent -- callers should
        surface this rather than let it pass silently (every row's review_date is absent in this
        case).

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

    # ── 4. Resolve date column (optional) ─────────────────────────────────────
    resolved_date: str | None = None
    if date_column:
        key = date_column.strip().lower()
        if key in header_lower:
            resolved_date = header_lower[key]
    else:
        for fallback in _FALLBACK_DATE_COLS:
            if fallback in header_lower:
                resolved_date = header_lower[fallback]
                break

    # ── 5. Stream-parse rows, enforce row cap ─────────────────────────────────
    rows: list[dict[str, str]] = []
    raw_dates: list[str] = []  # buffered for file-wide convention detection (step 6)
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
        if resolved_date and resolved_date in raw_row:
            raw_value = raw_row[resolved_date].strip()
            if raw_value:
                row["_raw_date"] = raw_value
                raw_dates.append(raw_value)
        rows.append(row)

    # ── 6. Resolve the file-wide day/month convention, then parse each row's date ──
    date_ambiguous = False
    if resolved_date:
        hint = {"dmy": True, "mdy": False}.get((date_format or "").strip().lower())
        dayfirst = hint if hint is not None else detect_dayfirst_convention(raw_dates)
        any_ambiguous_shape = any(
            _ambiguous_numeric_groups(v.strip()) is not None for v in raw_dates
        )
        date_ambiguous = dayfirst is None and any_ambiguous_shape

        for row in rows:
            raw_value = row.pop("_raw_date", None)
            if raw_value is None:
                continue
            parsed = parse_review_date(raw_value, dayfirst)
            if parsed is not None:
                row["review_date"] = parsed.isoformat()

    return rows, resolved_text, resolved_product, resolved_date, date_ambiguous
