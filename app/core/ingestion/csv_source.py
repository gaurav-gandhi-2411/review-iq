from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.csv_ingest import (
    CsvColumnError,
    FileTooLargeError,
    RowLimitExceededError,
    read_and_validate_csv,
)
from app.core.ingestion.base import SourceError

if TYPE_CHECKING:
    from fastapi import UploadFile


class CSVSource:
    """CSV-file review ingestion source wrapping read_and_validate_csv."""

    def __init__(
        self,
        file: UploadFile,
        text_column: str | None = None,
        product_column: str | None = None,
        date_column: str | None = None,
        date_format: str | None = None,
    ) -> None:
        self._file = file
        self._text_column = text_column
        self._product_column = product_column
        self._date_column = date_column
        self._date_format = date_format
        self._resolved_text: str | None = None
        self._resolved_product: str | None = None
        self._resolved_date: str | None = None
        self._date_ambiguous: bool = False

    @property
    def source_type(self) -> str:
        return "csv"

    async def fetch_reviews(self) -> list[dict[str, str | float | None]]:
        """Delegate to read_and_validate_csv; re-raise validation errors as SourceError."""
        try:
            rows, resolved_text, resolved_product, resolved_date, date_ambiguous = (
                await read_and_validate_csv(
                    self._file,
                    self._text_column,
                    self._product_column,
                    self._date_column,
                    self._date_format,
                )
            )
        except (FileTooLargeError, RowLimitExceededError, CsvColumnError) as exc:
            raise SourceError(str(exc)) from exc

        self._resolved_text = resolved_text
        self._resolved_product = resolved_product
        self._resolved_date = resolved_date
        self._date_ambiguous = date_ambiguous
        return rows  # type: ignore[return-value]

    def source_meta(self) -> dict[str, object]:
        return {
            "text_column": self._resolved_text,
            "product_column": self._resolved_product,
            "date_column": self._resolved_date,
            "date_ambiguous": self._date_ambiguous,
        }
