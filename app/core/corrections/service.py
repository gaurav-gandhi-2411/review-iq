from __future__ import annotations

from typing import Any

import psycopg2
import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)


def _db_connect() -> psycopg2.extensions.connection:
    settings = get_settings()
    return psycopg2.connect(settings.supabase_database_url)


def _set_tenant(cur: Any, org_id: str) -> None:
    """Set RLS context for the current transaction."""
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute('SET LOCAL "app.current_org_id" = %s', (org_id,))


def submit_correction_pg(
    org_id: str,
    review_id: str,
    source_type: str,
    field_path: str,
    original_value: str,
    corrected_value: str,
    correction_note: str | None,
    language: str,
) -> str:
    """Insert one correction row. Returns inserted id as str."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            """
            INSERT INTO public.corrections (
                org_id, review_id, source_type, field_path,
                original_value, corrected_value, correction_note, language
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                org_id,
                review_id,
                source_type,
                field_path,
                original_value,
                corrected_value,
                correction_note,
                language,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return str(row[0]) if row else ""
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_corrections_pg(
    org_id: str,
    *,
    source_type: str | None = None,
    review_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List corrections for this org, newest-first (ORDER BY corrected_at DESC).

    Optional filters: source_type (exact match), review_id (exact match).
    Always includes org_id = %s as first WHERE clause.
    LIMIT and OFFSET are appended last.
    """
    where: list[str] = ["org_id = %s"]
    params: list[Any] = [org_id]

    if source_type is not None:
        where.append("source_type = %s")
        params.append(source_type)
    if review_id is not None:
        where.append("review_id = %s")
        params.append(review_id)

    where_clause = "WHERE " + " AND ".join(where)
    params.extend([limit, offset])

    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            f"""
            SELECT id, org_id, review_id, source_type, field_path,
                   original_value, corrected_value, correction_note, language, corrected_at
            FROM public.corrections
            {where_clause}
            ORDER BY corrected_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        rows = cur.fetchall()
        conn.commit()
        cols = [
            "id",
            "org_id",
            "review_id",
            "source_type",
            "field_path",
            "original_value",
            "corrected_value",
            "correction_note",
            "language",
            "corrected_at",
        ]
        return [dict(zip(cols, row, strict=False)) for row in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
