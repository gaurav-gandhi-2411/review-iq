from __future__ import annotations

from benchmark.vernacular_v2.corpus_pipeline.language_strata import (
    ACTIVE_STRATA,
    SUPPORTED_STRATA,
    strata_summary,
    stratify,
)


def test_active_strata_is_en_hien_hi_only() -> None:
    assert set(ACTIVE_STRATA) == {"en", "hi-en", "hi"}


def test_planned_strata_present_but_not_active() -> None:
    planned = {s.code for s in SUPPORTED_STRATA if s.status == "planned"}
    assert planned == {"ta", "mr", "bn"}
    assert planned.isdisjoint(ACTIVE_STRATA)


def test_stratify_buckets_by_detected_language() -> None:
    records = [
        {"id": "1", "detected_language": "en"},
        {"id": "2", "detected_language": "hi-en"},
        {"id": "3", "detected_language": "en"},
        {"id": "4", "detected_language": "hi"},
    ]
    buckets = stratify(records)
    assert len(buckets["en"]) == 2
    assert len(buckets["hi-en"]) == 1
    assert len(buckets["hi"]) == 1


def test_stratify_unregistered_code_goes_to_unregistered_bucket() -> None:
    records = [{"id": "1", "detected_language": "fr"}]  # not registered at all
    buckets = stratify(records)
    assert "fr" not in buckets
    assert len(buckets["_unregistered"]) == 1


def test_stratify_custom_language_field() -> None:
    records = [{"id": "1", "lang": "hi"}]
    buckets = stratify(records, language_field="lang")
    assert len(buckets["hi"]) == 1


def test_strata_summary_flags_zero_record_active_strata() -> None:
    records = [{"id": "1", "detected_language": "en"}]
    summary = strata_summary(records)
    assert summary["total"] == 1
    assert "hi-en" in summary["active_strata_with_zero_records"]
    assert "hi" in summary["active_strata_with_zero_records"]
    assert "en" not in summary["active_strata_with_zero_records"]


def test_strata_summary_lists_planned_strata() -> None:
    summary = strata_summary([])
    assert set(summary["planned_strata_not_yet_supported"]) == {"ta", "mr", "bn"}
