"""Alert preferences + alert log storage — psycopg2, RLS-scoped via current_org_id().

All public functions follow the project pattern:
  _db_connect() → _set_tenant(cur, org_id) → query → commit/rollback → close.
"""

from __future__ import annotations

import json
from datetime import datetime
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


# ---------------------------------------------------------------------------
# Daily digest support — watermarks, since-queries, cross-org sweep
# ---------------------------------------------------------------------------


def get_last_digest_watermark_pg(org_id: str, event_type: str) -> datetime | None:
    """Return the most recent alert_log.sent_at for (org_id, event_type), or None.

    Used by the digest batcher as the lower bound ("since") for scanning
    extractions/authenticity_audits. This watermark is safe to use for that
    purpose even though it is only an efficiency bound, not a correctness
    guarantee: alert_log rows for an event_type currently configured
    daily_digest are only ever written by the digest batcher itself (the
    immediate engine explicitly skips record_alert_sent_pg when
    frequency == "daily_digest" — see engine.py's frequency gate) or by a
    prior era when the preference was "immediate" — either way, every
    alert_log row for (org, event_type) represents an event that was
    genuinely sent, so using its max sent_at as a window boundary cannot
    cause a drop. Any event NOT yet sent has no alert_log row and is picked
    up regardless of the watermark's exact value, because the final
    per-review exclusion check (is_already_alerted_pg) is the actual
    correctness guarantee here — this watermark only bounds how far back
    the query scans for efficiency.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT MAX(sent_at) FROM public.alert_log WHERE org_id = %s AND event_type = %s",
            (org_id, event_type),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_org_created_at_pg(org_id: str) -> datetime:
    """Return organizations.created_at for org_id.

    Used as the digest batcher's fallback "since" value when no prior
    digest has ever been sent for an (org, event_type) pair (i.e. no
    alert_log rows exist yet, so get_last_digest_watermark_pg returns None).

    Raises:
        ValueError: if org_id does not match a row. Should not happen for a
            valid org_id, but the service_role connection still deserves an
            explicit guard rather than a silent None or a crash on indexing.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT created_at FROM public.organizations WHERE id = %s",
            (org_id,),
        )
        row = cur.fetchone()
        conn.commit()
        if row is None:
            raise ValueError(f"organization {org_id!r} not found")
        created_at: datetime = row[0]
        return created_at
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_extractions_since_pg(org_id: str, since: datetime) -> list[dict[str, object]]:
    """Return extractions for org_id created after `since`, ordered oldest-first.

    Feeds the digest batcher's high_urgency re-evaluation: each row is passed
    through the pure rules layer (check_high_urgency) to decide inclusion.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT input_hash, product, urgency, topics, cons, created_at "
            "FROM public.extractions WHERE org_id = %s AND created_at > %s "
            "ORDER BY created_at",
            (org_id, since),
        )
        rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    def _load(val: Any) -> list[str]:
        if val is None:
            return []
        if isinstance(val, list):
            return val
        loaded: list[str] = json.loads(val)
        return loaded

    return [
        {
            "input_hash": r[0],
            "product": r[1],
            "urgency": r[2],
            "topics": _load(r[3]),
            "cons": _load(r[4]),
            "created_at": r[5],
        }
        for r in rows
    ]


def list_authenticity_audits_since_pg(org_id: str, since: datetime) -> list[dict[str, object]]:
    """Return authenticity_audits for org_id created after `since`, ordered oldest-first.

    Feeds the digest batcher's likely_fake re-evaluation: each row is passed
    through the pure rules layer (check_likely_fake) to decide inclusion.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT review_hash, score, label, flags, created_at "
            "FROM public.authenticity_audits WHERE org_id = %s AND created_at > %s "
            "ORDER BY created_at",
            (org_id, since),
        )
        rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return [
        {
            "review_hash": r[0],
            "score": float(r[1]),
            "label": str(r[2]),
            # authenticity_audits.flags is a TEXT column storing a JSON array
            # (not jsonb) — same defensive parse as get_authenticity_audit_by_hash_pg.
            "flags": json.loads(r[3]) if isinstance(r[3], str) else (r[3] or []),
            "created_at": r[4],
        }
        for r in rows
    ]


def list_orgs_with_daily_digest_pg() -> list[str]:
    """Return distinct org_ids with at least one enabled daily_digest preference.

    Cross-org query — connects via _db_connect() and does NOT call
    _set_tenant, the same pattern used in app/api/admin.py: the service_role
    connection bypasses RLS by design for this admin/scheduled-sweep use
    case, where there is no single org_id to scope the session to. Do not
    "fix" this by adding _set_tenant — it would break the query since it
    must see rows across all orgs.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT org_id FROM public.alert_preferences "
            "WHERE frequency = 'daily_digest' AND enabled = true"
        )
        rows = cur.fetchall()
        conn.commit()
        return [str(r[0]) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
