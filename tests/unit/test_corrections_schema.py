from __future__ import annotations

import pytest
from app.core.corrections.schema import (
    ALLOWED_FIELD_PATHS,
    Correction,
    SourceType,
    validate_field_path,
)
from pydantic import ValidationError


def _base_correction(**overrides: object) -> dict:
    base: dict = {
        "org_id": "org-123",
        "review_id": "a" * 64,
        "source_type": SourceType.extraction,
        "field_path": "sentiment",
        "original_value": "positive",
        "corrected_value": "negative",
    }
    base.update(overrides)
    return base


class TestSourceType:
    def test_values_are_strings(self) -> None:
        for member in SourceType:
            assert isinstance(member, str)


class TestAllowedFieldPaths:
    def test_all_source_types_covered(self) -> None:
        for st in SourceType:
            assert st in ALLOWED_FIELD_PATHS

    def test_extraction_has_expected_paths(self) -> None:
        paths = ALLOWED_FIELD_PATHS[SourceType.extraction]
        for expected in ("sentiment", "stars", "product", "pros", "cons"):
            assert expected in paths

    def test_authenticity_has_expected_paths(self) -> None:
        paths = ALLOWED_FIELD_PATHS[SourceType.authenticity]
        for expected in ("score", "label", "flags"):
            assert expected in paths

    def test_reply_has_expected_paths(self) -> None:
        paths = ALLOWED_FIELD_PATHS[SourceType.reply]
        for expected in ("reply_text", "tone"):
            assert expected in paths


class TestValidateFieldPath:
    def test_valid_extraction_path(self) -> None:
        validate_field_path(SourceType.extraction, "sentiment")

    def test_valid_authenticity_path(self) -> None:
        validate_field_path(SourceType.authenticity, "label")

    def test_valid_reply_path(self) -> None:
        validate_field_path(SourceType.reply, "tone")

    def test_invalid_path_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_field_path(SourceType.extraction, "nonexistent_field")

    def test_cross_type_path_raises(self) -> None:
        # "label" belongs to authenticity, not extraction
        with pytest.raises(ValueError):
            validate_field_path(SourceType.extraction, "label")

    def test_error_message_contains_field_path(self) -> None:
        with pytest.raises(ValueError, match="label"):
            validate_field_path(SourceType.extraction, "label")


class TestCorrectionModel:
    def test_valid_construction(self) -> None:
        c = Correction(**_base_correction())
        assert c.org_id == "org-123"
        assert c.source_type == SourceType.extraction
        assert c.field_path == "sentiment"

    def test_invalid_field_path_raises_on_construction(self) -> None:
        with pytest.raises(ValidationError):
            Correction(**_base_correction(field_path="nonexistent"))

    def test_review_id_rejects_prefixed_hash(self) -> None:
        with pytest.raises(ValidationError):
            Correction(**_base_correction(review_id="sha256:abc123"))

    def test_review_id_accepts_plain_hex(self) -> None:
        c = Correction(**_base_correction(review_id="a" * 64))
        assert c.review_id == "a" * 64

    def test_language_lowercased(self) -> None:
        c = Correction(**_base_correction(language="EN"))
        assert c.language == "en"

    def test_corrected_at_defaults(self) -> None:
        c = Correction(**_base_correction())
        assert c.corrected_at is not None

    def test_id_optional(self) -> None:
        c1 = Correction(**_base_correction())
        assert c1.id is None
        c2 = Correction(**_base_correction(id="some-uuid"))
        assert c2.id == "some-uuid"

    def test_correction_note_optional(self) -> None:
        c = Correction(**_base_correction())
        assert c.correction_note is None
