"""Dataset read model: fetch extractions joined with authenticity audits and corrections.

Each public function opens a fresh connection and sets RLS context before querying.
No DDL, no writes — this module is read-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import psycopg2

from app.core.config import get_settings


def _db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(get_settings().supabase_database_url)


def _set_tenant(cur: Any, org_id: str) -> None:
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute('SET LOCAL "app.current_org_id" = %s', (org_id,))


def _fetch_extractions_page(
    cur: Any,
    org_id: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Fetch one page of extractions for org_id, newest-first.

    RLS context must already be set on ``cur`` before calling this function.
    JSONB columns (topics, competitor_mentions, pros, cons, feature_requests) are
    returned as Python lists by psycopg2 when the column type is JSONB; the
    fallback json.loads handles TEXT columns that store JSON strings.

    Args:
        cur:     Open psycopg2 cursor with RLS already set.
        org_id:  Tenant identifier — also used in WHERE clause for defence-in-depth.
        limit:   Page size.
        offset:  Row offset.

    Returns:
        List of row dicts, one per extraction.
    """
    cur.execute(
        """
        SELECT id, org_id, review_id, review_text,
               product, stars, stars_inferred, buy_again, sentiment, urgency, language,
               review_length_chars, confidence, topics, competitor_mentions,
               pros, cons, feature_requests, model, prompt_version, schema_version,
               latency_ms, extracted_at, created_at, is_suspicious
        FROM public.extractions
        WHERE org_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        (org_id, limit, offset),
    )
    rows = cur.fetchall()
    cols = [
        "id",
        "org_id",
        "review_id",
        "review_text",
        "product",
        "stars",
        "stars_inferred",
        "buy_again",
        "sentiment",
        "urgency",
        "language",
        "review_length_chars",
        "confidence",
        "topics",
        "competitor_mentions",
        "pros",
        "cons",
        "feature_requests",
        "model",
        "prompt_version",
        "schema_version",
        "latency_ms",
        "extracted_at",
        "created_at",
        "is_suspicious",
    ]
    result: list[dict[str, Any]] = []
    for row in rows:
        record = dict(zip(cols, row, strict=False))
        # Normalise JSONB columns — psycopg2 returns lists for JSONB,
        # but falls back to a JSON string for TEXT columns.
        for col in ("topics", "competitor_mentions", "pros", "cons", "feature_requests"):
            val = record[col]
            if isinstance(val, str):
                record[col] = json.loads(val)
            elif val is None:
                record[col] = []
        result.append(record)
    return result


def _fetch_supporting_data(
    cur: Any,
    org_id: str,
    review_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Fetch authenticity audits and corrections for a set of review_ids.

    Both queries are issued in a single RLS-scoped cursor.  RLS context must
    already be set before calling this function.

    Authenticity deduplication: if (somehow) multiple audit rows exist for the
    same review_id, only the one with the latest created_at is kept.

    Args:
        cur:        Open psycopg2 cursor with RLS already set.
        org_id:     Tenant identifier — used in WHERE clause for defence-in-depth.
        review_ids: List of review_id values to look up.

    Returns:
        Tuple of:
          auth_by_review_id   — dict[review_id → {score, label, flags, scored_at}]
          corrections_by_review_id — dict[review_id → list[correction_dict]]
    """
    # --- Query 1: authenticity_audits ---
    cur.execute(
        """
        SELECT review_id, score, label, flags, created_at
        FROM public.authenticity_audits
        WHERE org_id = %s AND review_id = ANY(%s)
        """,
        (org_id, review_ids),
    )
    auth_rows = cur.fetchall()

    auth_by_review_id: dict[str, dict[str, Any]] = {}
    for review_id, score, label, flags_raw, created_at in auth_rows:
        flags: list[str] = (
            json.loads(flags_raw) if isinstance(flags_raw, str) else (flags_raw or [])
        )
        candidate: dict[str, Any] = {
            "score": float(score) if score is not None else None,
            "label": label,
            "flags": flags,
            "scored_at": created_at,
        }
        existing = auth_by_review_id.get(review_id)
        # Keep the row with the latest created_at (guard against duplicates).
        if existing is None or (
            created_at is not None
            and existing["scored_at"] is not None
            and created_at > existing["scored_at"]
        ):
            auth_by_review_id[review_id] = candidate

    # --- Query 2: corrections ---
    cur.execute(
        """
        SELECT id, review_id, source_type, field_path, original_value, corrected_value,
               correction_note, language, corrected_at
        FROM public.corrections
        WHERE org_id = %s AND review_id = ANY(%s)
        ORDER BY corrected_at DESC
        """,
        (org_id, review_ids),
    )
    corrections_rows = cur.fetchall()
    corrections_cols = [
        "id",
        "review_id",
        "source_type",
        "field_path",
        "original_value",
        "corrected_value",
        "correction_note",
        "language",
        "corrected_at",
    ]

    corrections_by_review_id: dict[str, list[dict[str, Any]]] = {}
    for row in corrections_rows:
        c = dict(zip(corrections_cols, row, strict=False))
        rid = c["review_id"]
        corrections_by_review_id.setdefault(rid, []).append(c)

    return auth_by_review_id, corrections_by_review_id


def get_dataset_page(
    org_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return one page of the org's structured review dataset.

    Opens a fresh connection, sets RLS, fetches extractions, then enriches each
    record with its authenticity audit (if scored) and any corrections.

    Args:
        org_id: Tenant identifier.
        limit:  Page size (default 50).
        offset: Row offset for pagination (default 0).

    Returns:
        List of assembled record dicts.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)

        rows = _fetch_extractions_page(cur, org_id, limit, offset)

        review_ids = [r["review_id"] for r in rows if r.get("review_id")]
        if review_ids:
            auth_by_review_id, corrections_by_review_id = _fetch_supporting_data(
                cur, org_id, review_ids
            )
        else:
            auth_by_review_id = {}
            corrections_by_review_id = {}

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    records: list[dict[str, Any]] = []
    for row in rows:
        rid = row["review_id"]
        records.append(
            {
                "review_id": rid,
                "review_text": row["review_text"],
                "extracted_at": row["extracted_at"],
                "created_at": row["created_at"],
                "extraction": {
                    "product": row["product"],
                    "stars": row["stars"],
                    "stars_inferred": row["stars_inferred"],
                    "buy_again": row["buy_again"],
                    "sentiment": row["sentiment"],
                    "urgency": row["urgency"],
                    "language": row["language"],
                    "review_length_chars": row["review_length_chars"],
                    "confidence": row["confidence"],
                    "topics": row["topics"],
                    "competitor_mentions": row["competitor_mentions"],
                    "pros": row["pros"],
                    "cons": row["cons"],
                    "feature_requests": row["feature_requests"],
                    "model": row["model"],
                    "prompt_version": row["prompt_version"],
                    "is_suspicious": row["is_suspicious"],
                },
                "authenticity": auth_by_review_id.get(rid),
                "corrections": corrections_by_review_id.get(rid, []),
            }
        )
    return records


def iter_dataset_jsonl(org_id: str, batch_size: int = 100) -> Iterator[str]:
    """Yield JSONL lines for the org's full dataset, fetched in batches.

    Each line is a JSON-serialised record followed by a newline.
    ``default=str`` handles datetime serialisation without raising.

    Args:
        org_id:     Tenant identifier.
        batch_size: Number of extractions to fetch per DB round-trip (default 100).

    Yields:
        ``json.dumps(record, default=str) + "\\n"`` for each record.
    """
    offset = 0
    while True:
        batch = get_dataset_page(org_id, limit=batch_size, offset=offset)
        for record in batch:
            yield json.dumps(record, default=str) + "\n"
        if len(batch) < batch_size:
            break
        offset += batch_size
