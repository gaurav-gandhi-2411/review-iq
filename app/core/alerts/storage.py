"""Alert preferences + alert log storage — psycopg2, RLS-scoped via current_org_id().

All public functions follow the project pattern:
  _db_connect() → _set_tenant(cur, org_id) → query → commit/rollback → close.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg2
import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)

_ALL_EVENT_TYPES: tuple[str, ...] = (
    "high_urgency",
    "likely_fake",
    "fake_cluster",
    "topic_spike",
)
_DEFAULT_ENABLED = True
_DEFAULT_FREQUENCY = "immediate"


def _db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(get_settings().supabase_database_url)


def _set_tenant(cur: Any, org_id: str) -> None:
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute('SET LOCAL "app.current_org_id" = %s', (org_id,))


# ---------------------------------------------------------------------------
# Notification email (stored on organizations table)
# ---------------------------------------------------------------------------


def get_org_notification_email_pg(org_id: str) -> str | None:
    """Return the alert notification email for this org, or None if not set."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT notification_email FROM public.organizations WHERE id = %s",
            (org_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row and row[0] else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_org_notification_email_pg(org_id: str, email: str | None) -> None:
    """Update the notification email for this org."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "UPDATE public.organizations SET notification_email = %s WHERE id = %s",
            (email, org_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert preferences
# ---------------------------------------------------------------------------


def get_preference_pg(org_id: str, event_type: str) -> dict[str, object] | None:
    """Return preference for one event type, or None if not explicitly set (use defaults)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT event_type, enabled, frequency "
            "FROM public.alert_preferences WHERE org_id = %s AND event_type = %s",
            (org_id, event_type),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        return {"event_type": row[0], "enabled": row[1], "frequency": row[2]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_all_preferences_pg(org_id: str) -> list[dict[str, object]]:
    """Return preferences for all event types, filling missing types with defaults."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT event_type, enabled, frequency, updated_at "
            "FROM public.alert_preferences WHERE org_id = %s",
            (org_id,),
        )
        rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    existing = {
        r[0]: {
            "event_type": r[0],
            "enabled": r[1],
            "frequency": r[2],
            "updated_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    }
    return [
        existing.get(
            et,
            {
                "event_type": et,
                "enabled": _DEFAULT_ENABLED,
                "frequency": _DEFAULT_FREQUENCY,
                "updated_at": None,
            },
        )
        for et in _ALL_EVENT_TYPES
    ]


def upsert_preference_pg(org_id: str, event_type: str, enabled: bool, frequency: str) -> None:
    """Insert or update a single event-type preference for this org."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            """
            INSERT INTO public.alert_preferences (org_id, event_type, enabled, frequency, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (org_id, event_type) DO UPDATE
                SET enabled    = EXCLUDED.enabled,
                    frequency  = EXCLUDED.frequency,
                    updated_at = now()
            """,
            (org_id, event_type, enabled, frequency),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert log — dedupe + digest batching
# ---------------------------------------------------------------------------


def is_already_alerted_pg(org_id: str, review_id: str, event_type: str) -> bool:
    """Return True if an alert was already sent for this review+event_type."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT 1 FROM public.alert_log "
            "WHERE org_id = %s AND review_id = %s AND event_type = %s LIMIT 1",
            (org_id, review_id, event_type),
        )
        row = cur.fetchone()
        conn.commit()
        return row is not None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_alert_sent_pg(
    org_id: str,
    review_id: str | None,
    event_type: str,
    details: dict[str, object],
) -> None:
    """Append an alert_log row for dedupe and audit."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "INSERT INTO public.alert_log (org_id, review_id, event_type, details) "
            "VALUES (%s, %s, %s, %s)",
            (org_id, review_id, event_type, json.dumps(details)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
