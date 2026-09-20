"""public.leads grants + RLS + CHECK constraints (Session 15c C9).

Proves, against a real Postgres with every migration applied (the pre-cutover-verification
job's throwaway container), that the SQL in 20260920000001_leads.sql does what the unit
tests can only assume: review_iq_app can insert a lead and flip its email_status, and
nothing more -- no full-row read, no other-column update, no delete, no touching
historical leads -- while anon/authenticated hold no access at all.

Connects with SUPABASE_DATABASE_URL (review_iq_app) for the app-side assertions and
SUPABASE_DIRECT_URL (superuser) for seeding/cleanup, same split as test_role_bypassrls.py.
Every seeded row uses an @example.invalid address and is deleted in the fixture teardown.

Marked 'integration' -- skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_leads_rls.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

from app.core.storage_pg import (  # noqa: E402
    insert_lead_pg,
    update_lead_email_status_pg,
)


def _app_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"], connect_timeout=15)


def _superuser_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"], connect_timeout=15)


def _super_fetch(sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


def _as_app(sql: str, params: tuple[object, ...] = ()) -> None:
    """Run one statement as review_iq_app on its own connection; raises on DB error."""
    conn = _app_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _insert(lead_id: str, **overrides: object) -> None:
    fields: dict[str, object] = {
        "name": "RLS Test",
        "email": f"rls-{lead_id[:8]}@example.invalid",
        "company": "RLS Test Co",
        "brands": "TestBrand",
        "reviews_per_month": "1000-5000",
        "message": None,
        "source_ip_hash": None,
        "user_agent": None,
    }
    fields.update(overrides)
    insert_lead_pg(lead_id, **fields)  # type: ignore[arg-type]


@pytest.fixture
def lead_ids() -> Iterator[list[str]]:
    """Collects ids created by a test; deletes them (as superuser) afterwards."""
    ids: list[str] = []
    yield ids
    if ids:
        conn = _superuser_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM public.leads WHERE id = ANY(%s::uuid[])", (ids,))
            conn.commit()
        finally:
            conn.close()


@pytest.mark.integration
class TestLeadsGrantsAndRls:
    def test_app_role_can_insert_then_update_status(self, lead_ids: list[str]) -> None:
        lead_id = str(uuid.uuid4())
        lead_ids.append(lead_id)
        _insert(lead_id, message="hello\nworld", source_ip_hash="a" * 64, user_agent="ua")

        rows = _super_fetch(
            "SELECT name, email_status, message, source_ip_hash FROM public.leads WHERE id = %s",
            (lead_id,),
        )
        assert rows == [("RLS Test", "pending", "hello\nworld", "a" * 64)]

        assert update_lead_email_status_pg(lead_id, "sent") is True
        assert _super_fetch("SELECT email_status FROM public.leads WHERE id = %s", (lead_id,)) == [
            ("sent",)
        ]

    def test_app_role_cannot_read_lead_pii(self, lead_ids: list[str]) -> None:
        lead_id = str(uuid.uuid4())
        lead_ids.append(lead_id)
        _insert(lead_id)
        for sql in (
            "SELECT * FROM public.leads",
            "SELECT email FROM public.leads",
            "SELECT name, message FROM public.leads WHERE id = %s",
        ):
            params = (lead_id,) if "%s" in sql else ()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                _as_app(sql, params)
        # The two granted columns are readable.
        _as_app("SELECT id, email_status FROM public.leads WHERE id = %s", (lead_id,))

    def test_app_role_cannot_delete_or_update_other_columns(self, lead_ids: list[str]) -> None:
        lead_id = str(uuid.uuid4())
        lead_ids.append(lead_id)
        _insert(lead_id)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            _as_app("DELETE FROM public.leads WHERE id = %s", (lead_id,))
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            _as_app("UPDATE public.leads SET name = 'x' WHERE id = %s", (lead_id,))

    def test_app_role_cannot_reset_status_to_pending(self, lead_ids: list[str]) -> None:
        lead_id = str(uuid.uuid4())
        lead_ids.append(lead_id)
        _insert(lead_id)
        assert update_lead_email_status_pg(lead_id, "failed") is True
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            # RLS WITH CHECK (email_status <> 'pending') -> "new row violates row-level
            # security policy" (SQLSTATE 42501, the same class).
            _as_app("UPDATE public.leads SET email_status = 'pending' WHERE id = %s", (lead_id,))

    def test_app_role_cannot_touch_historical_leads(self, lead_ids: list[str]) -> None:
        """The one-hour policy window: an old lead is invisible to review_iq_app, so the
        status update matches zero rows (returns False) rather than rewriting history."""
        lead_id = str(uuid.uuid4())
        lead_ids.append(lead_id)
        conn = _superuser_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.leads (id, created_at, name, email, company, brands, "
                "reviews_per_month) VALUES (%s, now() - interval '2 hours', 'Old', "
                "'old@example.invalid', 'Old Co', 'B', '1-10')",
                (lead_id,),
            )
            conn.commit()
        finally:
            conn.close()
        assert update_lead_email_status_pg(lead_id, "sent") is False
        assert _super_fetch("SELECT email_status FROM public.leads WHERE id = %s", (lead_id,)) == [
            ("pending",)
        ]

    def test_app_role_insert_must_start_pending(self, lead_ids: list[str]) -> None:
        lead_id = str(uuid.uuid4())
        lead_ids.append(lead_id)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            _as_app(
                "INSERT INTO public.leads (id, name, email, company, brands, reviews_per_month, "
                "email_status) VALUES (%s, 'n', 'n@example.invalid', 'c', 'b', '1', 'sent')",
                (lead_id,),
            )

    def test_anon_and_authenticated_hold_no_privileges(self) -> None:
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                rows = _super_fetch(
                    "SELECT has_table_privilege(%s, 'public.leads', %s)", (role, priv)
                )
                assert rows == [(False,)], f"{role} unexpectedly holds {priv} on public.leads"

    def test_rls_is_enabled(self) -> None:
        rows = _super_fetch(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.leads'::regclass"
        )
        assert rows == [(True,)]


@pytest.mark.integration
class TestLeadsCheckConstraints:
    @pytest.mark.parametrize(
        ("column", "bad_value"),
        [
            ("name", "Bob\r\nBcc: x@example.invalid"),
            ("company", "a\x07b"),
            ("email", "not-an-email"),
            ("email", "a b@example.invalid"),
            ("brands", ""),
            ("reviews_per_month", "x" * 51),
            ("message", "bad\x07bell"),
            ("source_ip_hash", "not-a-hash"),
            ("email_status", "bogus"),
        ],
    )
    def test_check_constraint_rejects(self, column: str, bad_value: str) -> None:
        lead_id = str(uuid.uuid4())
        values: dict[str, object] = {
            "id": lead_id,
            "name": "n",
            "email": "ok@example.invalid",
            "company": "c",
            "brands": "b",
            "reviews_per_month": "1",
        }
        values[column] = bad_value
        cols = ", ".join(values)
        placeholders = ", ".join(["%s"] * len(values))
        conn = _superuser_conn()
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(
                    f"INSERT INTO public.leads ({cols}) VALUES ({placeholders})",  # noqa: S608
                    tuple(values.values()),
                )
        finally:
            conn.rollback()
            conn.close()
