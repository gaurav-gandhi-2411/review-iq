"""Wave 1 Section H: corpus_pipeline.pii_scrub -- thin wrapper over the REAL
app.core.sanitize.redact_pii(), never a parallel implementation. These tests exercise
the wrapper's own logic (field naming, summary counts) using real redact_pii() calls
-- no mocking needed, it's pure regex, no network."""

from __future__ import annotations

from benchmark.vernacular_v2.corpus_pipeline.pii_scrub import scrub_record, scrub_records


def test_scrub_record_redacts_email() -> None:
    rec = {"id": "1", "text": "contact me at foo@example.com please"}
    out = scrub_record(rec)
    assert "[EMAIL]" in out["text"]
    assert "foo@example.com" not in out["text"]
    assert out["text_pii_redaction_count"] == 1
    assert out["pii_scrubbed"] is True


def test_scrub_record_no_pii_present() -> None:
    rec = {"id": "1", "text": "great product, works well"}
    out = scrub_record(rec)
    assert out["text"] == "great product, works well"
    assert out["text_pii_redaction_count"] == 0
    assert out["pii_scrubbed"] is True


def test_scrub_record_does_not_mutate_input() -> None:
    rec = {"id": "1", "text": "email me at a@b.com"}
    scrub_record(rec)
    assert rec["text"] == "email me at a@b.com"  # original untouched


def test_scrub_record_custom_text_field() -> None:
    rec = {"id": "1", "review_body": "email a@b.com now"}
    out = scrub_record(rec, text_field="review_body")
    assert "[EMAIL]" in out["review_body"]
    assert out["review_body_pii_redaction_count"] == 1


def test_scrub_record_preserves_other_fields() -> None:
    rec = {"id": "1", "text": "fine product", "rating": 5, "source": "kaggle/x"}
    out = scrub_record(rec)
    assert out["rating"] == 5
    assert out["source"] == "kaggle/x"


def test_scrub_records_summary_counts() -> None:
    records = [
        {"id": "1", "text": "contact me at foo@example.com"},
        {"id": "2", "text": "no pii here at all"},
        {"id": "3", "text": "reach bar@baz.org for support"},
    ]
    scrubbed, summary = scrub_records(records)
    assert len(scrubbed) == 3
    assert summary["total_records"] == 3
    assert summary["total_pii_spans_redacted"] == 2
    assert summary["records_with_at_least_one_redaction"] == 2
    assert summary["records_with_at_least_one_redaction_pct"] == round(100 * 2 / 3, 3)


def test_scrub_records_empty_list() -> None:
    scrubbed, summary = scrub_records([])
    assert scrubbed == []
    assert summary["total_records"] == 0
    assert summary["records_with_at_least_one_redaction_pct"] == 0.0
