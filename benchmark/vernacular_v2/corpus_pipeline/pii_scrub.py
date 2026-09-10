"""PII scrub at ingest — calls the real `app.core.sanitize.redact_pii()`.

Deliberately NOT a second, parallel PII-scrubbing implementation. This module is a
thin batch wrapper: it calls the exact same function `/v2/extract` calls before any
review text reaches a third-party LLM, so the corpus is scrubbed by the same logic
that already protects production traffic — one PII-redaction implementation, one
place to fix it, one recall number that means the same thing everywhere it's quoted.

Known limitation, carried forward honestly (see Wave 1 Section E's own audit,
`plan.md`'s "E kickoff" note): `redact_pii()` is DESTRUCTIVE replacement
(`[EMAIL]`/`[PHONE]`/`[CARD]`/name-intro-only), not the reversible token map the Wave
1 spec's Section E calls for, and it has no order/invoice-ID pattern. This module
inherits that exact limitation — it does not attempt to fix or work around it, since
Section E owns the underlying `redact_pii()` implementation and reworking it here
would be scope creep into a different section AND would create the parallel-
implementation problem this module explicitly avoids. If Section E's rework lands
(reversible token map, wider PII coverage), THIS module's scrub quality improves for
free on the next corpus-ingest run — no changes needed here, since it calls
`redact_pii()` by reference, not a frozen copy of its logic.
"""

from __future__ import annotations

from app.core.sanitize import redact_pii


def scrub_record(record: dict, *, text_field: str = "text") -> dict:
    """Return a copy of `record` with `record[text_field]` PII-redacted.

    Adds two provenance fields:
      - `{text_field}_pii_redaction_count`: how many PII spans were removed.
      - `pii_scrubbed`: True (always, once this function has run) — lets downstream
        consumers assert a record actually passed through this stage rather than
        silently trusting an unscrubbed field.

    The original unredacted text is NOT retained on the returned record — corpus
    records are meant to leave this stage in the state they're safe to persist/
    label/ship, not to carry a raw-PII field alongside the scrubbed one.
    """
    text = record.get(text_field, "")
    redacted, count = redact_pii(text)
    out = dict(record)
    out[text_field] = redacted
    out[f"{text_field}_pii_redaction_count"] = count
    out["pii_scrubbed"] = True
    return out


def scrub_records(records: list[dict], *, text_field: str = "text") -> tuple[list[dict], dict]:
    """Scrub every record; return (scrubbed_records, summary).

    Summary reports total spans redacted and how many records had >=1 redaction —
    this becomes the "measure redaction recall on a labeled set" sales-asset number
    Section E's spec item calls for, once run against a set with known-planted PII
    (that labeled set is Section E's responsibility to build; this function reports
    whatever it's given honestly, it does not claim a recall number on its own).
    """
    scrubbed = [scrub_record(r, text_field=text_field) for r in records]
    total_redactions = sum(r[f"{text_field}_pii_redaction_count"] for r in scrubbed)
    n_with_redaction = sum(1 for r in scrubbed if r[f"{text_field}_pii_redaction_count"] > 0)
    summary = {
        "total_records": len(records),
        "total_pii_spans_redacted": total_redactions,
        "records_with_at_least_one_redaction": n_with_redaction,
        "records_with_at_least_one_redaction_pct": (
            round(100 * n_with_redaction / len(records), 3) if records else 0.0
        ),
    }
    return scrubbed, summary
