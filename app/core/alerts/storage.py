"""Alert preferences + alert log storage — psycopg2, RLS-scoped via current_org_id().

All public functions follow the project pattern:
  _db_connect() → _set_tenant(cur, org_id) → query → commit/rollback → close.
"""

from __future__ import annotations

import asyncio
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
# Email suppressions (hard bounces / complaints reported by the Resend webhook)
# ---------------------------------------------------------------------------
# public.email_suppressions is unreadable by every app role; access is only through the
# SECURITY DEFINER functions of 20261009000001_email_suppressions.sql. No tenant scoping
# applies (a bounce is keyed by address, not by org) -- see ALLOWLIST in
# scripts/check_undocumented_pg_connects.py.


def is_email_suppressed_pg(email: str) -> bool:
    """True iff this address previously hard-bounced or complained (hash lookup)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT public.is_email_suppressed(%s)", (email,))
        row = cur.fetchone()
        conn.commit()
        return bool(row and row[0])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_orgs_for_notification_email_pg(email: str) -> list[str]:
    """Org ids whose notification_email equals this address (case-insensitive)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT public.resolve_orgs_for_notification_email(%s)", (email,))
        rows = cur.fetchall()
        conn.commit()
        return [str(r[0]) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_email_suppression_pg(event_id: str, email: str, reason: str) -> bool:
    """Record a suppression; True if newly inserted, False for a replayed event_id."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT public.record_email_suppression(%s, %s, %s)", (event_id, email, reason))
        row = cur.fetchone()
        conn.commit()
        return bool(row and row[0])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def recipient_is_suppressed(email: str, *, org_id: str) -> bool:
    """Sender-side guard shared by the alert engine and the digest.

    Fails closed: a lookup error means "treat as suppressed" for this send (logged), except
    when the migration is not applied yet (UndefinedFunction), where sending proceeds so
    deploying the code before the migration does not stop all alert mail.
    """
    try:
        return await asyncio.to_thread(is_email_suppressed_pg, email)
    except psycopg2.errors.UndefinedFunction:
        log.warning("email_suppression.function_missing", org_id=org_id)
        return False
    except Exception:
        log.error("email_suppression.lookup_failed", org_id=org_id, exc_info=True)
        return True


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


def get_last_digest_watermark_pg(
    org_id: str, event_type: str, cadence: str | None = None
) -> datetime | None:
    """Return the most recent alert_log.sent_at for (org_id, event_type), or None.

    cadence=None (the daily digest) keeps the original query unchanged. A non-None cadence
    (weekly) restricts to alert_log rows whose details->>'cadence' matches, so a weekly run's
    watermark is not advanced by daily/immediate rows and vice versa. Rows written before
    cadence existed carry no key and never match a cadence filter; the weekly sweep then falls
    back to org creation and relies on the per-review is_already_alerted_pg exclusion.

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
        if cadence is None:
            cur.execute(
                "SELECT MAX(sent_at) FROM public.alert_log WHERE org_id = %s AND event_type = %s",
                (org_id, event_type),
            )
        else:
            cur.execute(
                "SELECT MAX(sent_at) FROM public.alert_log "
                "WHERE org_id = %s AND event_type = %s AND details->>'cadence' = %s",
                (org_id, event_type, cadence),
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

    Cross-org query — connects via _db_connect() (review_iq_app), which holds no direct
    SELECT on alert_preferences and no BYPASSRLS. NOT the same mechanism as
    app/api/admin.py (that connects via ADMIN_DATABASE_URL as review_iq_admin, a distinct
    role that keeps its own BYPASSRLS -- this file never has). Calls
    public.list_orgs_with_daily_digest(), a narrow SECURITY DEFINER function
    (20260817000003) added to replace the BYPASSRLS-dependent raw query this used to run --
    confirmed via a real container test that the equivalent raw query on the sibling
    extractions sweep silently returns 0 rows post-cutover despite rows existing. Do not
    replace this call with a raw SELECT against alert_preferences.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT org_id FROM public.list_orgs_with_daily_digest()")
        rows = cur.fetchall()
        conn.commit()
        return [str(r[0]) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Urgent-alert coalescing -- window state + deferred events, all in alert_log
# ---------------------------------------------------------------------------
# No new table: alert_log is append-only (SELECT/INSERT only), so window state is encoded as
# marker rows with their own event_types, which also keeps them out of every existing query
# (dedupe and the digest watermark both filter on event_type = 'high_urgency'/'likely_fake').
#   urgent_window_claim    review_id = claim token. One per immediate send attempt.
#   urgent_window_release  review_id = same token. Written when the send failed, so a failed
#                          send does not consume the window.
#   urgent_deferred        review_id = the review. An urgent event held back by the cap.
#                          Pending until a 'high_urgency' row (sent) exists for the same review.

URGENT_CLAIM_EVENT = "urgent_window_claim"
URGENT_RELEASE_EVENT = "urgent_window_release"
URGENT_DEFERRED_EVENT = "urgent_deferred"


def claim_urgent_window_pg(
    org_id: str, token: str, window_minutes: int, max_per_window: int
) -> bool:
    """Atomically claim one immediate-urgent slot for this org. True = caller may send now.

    A transaction-scoped advisory lock keyed on org_id serialises concurrent ingest workers
    for the SAME org only (the lock is held for this short count+insert, never across the
    network send). Active claims = claim rows inside the window with no matching release row.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"urgent-window:{org_id}",))
        cur.execute(
            "SELECT count(*) FROM public.alert_log c "
            "WHERE c.org_id = %s AND c.event_type = %s "
            "AND c.sent_at > now() - make_interval(mins => %s) "
            "AND NOT EXISTS (SELECT 1 FROM public.alert_log r WHERE r.org_id = c.org_id "
            "AND r.event_type = %s AND r.review_id = c.review_id)",
            (org_id, URGENT_CLAIM_EVENT, window_minutes, URGENT_RELEASE_EVENT),
        )
        row = cur.fetchone()
        active = int(row[0]) if row else 0
        claimed = active < max_per_window
        if claimed:
            cur.execute(
                "INSERT INTO public.alert_log (org_id, review_id, event_type, details) "
                "VALUES (%s, %s, %s, '{}')",
                (org_id, token, URGENT_CLAIM_EVENT),
            )
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_urgent_window_pg(org_id: str, token: str) -> None:
    """Give back a claimed slot (the send failed), so the window is not consumed by nothing."""
    record_alert_sent_pg(org_id, token, URGENT_RELEASE_EVENT, {})


def record_urgent_deferred_pg(org_id: str, review_id: str, details: dict[str, object]) -> None:
    """Record an urgent event held back by the cap (idempotent per review)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "INSERT INTO public.alert_log (org_id, review_id, event_type, details) "
            "SELECT %s, %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM public.alert_log "
            "WHERE org_id = %s AND review_id = %s AND event_type = %s)",
            (
                org_id,
                review_id,
                URGENT_DEFERRED_EVENT,
                json.dumps(details),
                org_id,
                review_id,
                URGENT_DEFERRED_EVENT,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_pending_urgent_deferred_pg(org_id: str) -> list[dict[str, object]]:
    """Deferred urgent events not yet delivered (no 'high_urgency' row for the review)."""
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT DISTINCT ON (d.review_id) d.review_id, d.details, d.sent_at "
            "FROM public.alert_log d WHERE d.org_id = %s AND d.event_type = %s "
            "AND NOT EXISTS (SELECT 1 FROM public.alert_log s WHERE s.org_id = d.org_id "
            "AND s.review_id = d.review_id AND s.event_type = 'high_urgency') "
            "ORDER BY d.review_id, d.sent_at",
            (org_id, URGENT_DEFERRED_EVENT),
        )
        rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    out: list[dict[str, object]] = []
    for r in rows:
        details = r[1] if isinstance(r[1], dict) else json.loads(r[1] or "{}")
        out.append({"review_id": str(r[0]), "details": details, "deferred_at": r[2]})
    return out


def list_orgs_with_deferred_urgent_pg() -> list[str]:
    """Distinct org_ids holding at least one undelivered deferred urgent event.

    Cross-org sweep: goes through public.list_orgs_with_deferred_urgent_alerts(), a narrow
    SECURITY DEFINER function (migration 20261009000002) -- same pattern and reasoning as
    list_orgs_with_daily_digest_pg above. Never replace with a raw SELECT on alert_log.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT org_id FROM public.list_orgs_with_deferred_urgent_alerts()")
        rows = cur.fetchall()
        conn.commit()
        return [str(r[0]) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_orgs_with_weekly_digest_pg() -> list[str]:
    """Return distinct org_ids with at least one enabled weekly_digest preference.

    Same mechanism and constraints as list_orgs_with_daily_digest_pg: calls the narrow
    SECURITY DEFINER function public.list_orgs_with_weekly_digest() (migration
    20261009000003), never a raw SELECT on alert_preferences. Before that migration is applied
    the function does not exist and this raises, which the sweep endpoint surfaces as an
    error rather than as "no orgs".
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT org_id FROM public.list_orgs_with_weekly_digest()")
        rows = cur.fetchall()
        conn.commit()
        return [str(r[0]) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_extraction_summaries_since_pg(org_id: str, since: datetime) -> list[dict[str, object]]:
    """Return (urgency, topics, cons, created_at) for every extraction after `since`.

    Feeds the weekly digest's week-over-week counts; aggregation is done in Python by
    digest.compute_weekly_stats so the SQL stays a plain tenant-scoped read.
    """
    conn = _db_connect()
    try:
        cur = conn.cursor()
        _set_tenant(cur, org_id)
        cur.execute(
            "SELECT urgency, topics, cons, created_at "
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
        {"urgency": r[0], "topics": _load(r[1]), "cons": _load(r[2]), "created_at": r[3]}
        for r in rows
    ]
