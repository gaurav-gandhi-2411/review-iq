"""Language stratification registry — en / hi-en / hi today, ta / mr / bn later.

Reuses the SAME production detector `app.core.language.detect_language()` that
`benchmark/vernacular_v2/classify_language.py` already applies (measures what the
live system would actually route, per that script's own docstring) — this module
does not re-detect anything, it buckets records that already carry a
`detected_language` field.

Why a registry instead of scattering `if lang == "hi-en": ...` chains across the
pipeline (as `isolate_vernacular.py` already does, for its narrower purpose): adding
a new stratum later means adding one entry here, not hunting down every place a
language code is compared by hand. The two-step reality of language expansion is
disclosed, not hidden: (1) `app.core.language.detect_language()` needs real
detection logic for the new language (Devanagari-only regex heuristics don't
generalize to Tamil/Marathi/Bengali scripts) — that is out of scope for this
section and belongs to whoever ships the detector change; (2) once a new code is
returned by the detector, registering it here is the only pipeline-side change
needed for stratification to pick it up.
"""

from __future__ import annotations

from dataclasses import dataclass

DetectedLanguageCode = str  # matches app.core.language.DetectedLanguage's runtime values


@dataclass(frozen=True)
class LanguageStratum:
    code: DetectedLanguageCode
    display_name: str
    status: str  # "active" | "planned"


# Registration order = display order. Extend this tuple (not the pipeline logic) to
# add ta/mr/bn once app.core.language.detect_language() can return those codes.
SUPPORTED_STRATA: tuple[LanguageStratum, ...] = (
    LanguageStratum("en", "English", status="active"),
    LanguageStratum("hi-en", "Hinglish (Latin-script code-mixed)", status="active"),
    LanguageStratum("hi", "Hindi (Devanagari)", status="active"),
    LanguageStratum("ta", "Tamil", status="planned"),
    LanguageStratum("mr", "Marathi", status="planned"),
    LanguageStratum("bn", "Bengali", status="planned"),
)

ACTIVE_STRATA: tuple[str, ...] = tuple(s.code for s in SUPPORTED_STRATA if s.status == "active")


def stratify(
    records: list[dict], *, language_field: str = "detected_language"
) -> dict[str, list[dict]]:
    """Bucket `records` by `record[language_field]`.

    Records whose language code isn't in `SUPPORTED_STRATA` at all (not even
    "planned") land in a synthetic "_unregistered" bucket rather than being
    silently dropped — surfaces detector output this registry hasn't been told
    about yet, instead of hiding it.

    Records with an "active" stratum but no data yet still get an (empty-safe)
    entry when present in `records`; a "planned" stratum with zero matching
    records simply won't appear as a key (nothing to report).
    """
    known_codes = {s.code for s in SUPPORTED_STRATA}
    buckets: dict[str, list[dict]] = {}
    for rec in records:
        code = rec.get(language_field)
        key = code if code in known_codes else "_unregistered"
        buckets.setdefault(key, []).append(rec)
    return buckets


def strata_summary(records: list[dict], *, language_field: str = "detected_language") -> dict:
    """Counts per stratum + which registered strata have zero data — a quick sanity
    check before committing a corpus snapshot ("did the hi bucket silently empty?")."""
    buckets = stratify(records, language_field=language_field)
    counts = {code: len(items) for code, items in buckets.items()}
    return {
        "total": len(records),
        "counts": counts,
        "active_strata_with_zero_records": [
            s.code for s in SUPPORTED_STRATA if s.status == "active" and counts.get(s.code, 0) == 0
        ],
        "planned_strata_not_yet_supported": [
            s.code for s in SUPPORTED_STRATA if s.status == "planned"
        ],
    }
