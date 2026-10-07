"""public.get_last_seen / public.touch_last_seen behaviour (S18 D3, ADR 0036).

Proves against a real Postgres with every migration applied (the pre-cutover-verification
job's throwaway container) what the unit tests can only assume: review_iq_app can read and
touch last_seen_at through the two SECURITY DEFINER functions and has no direct table access,
touch is debounced to one write per 60 seconds, and an unknown user gets NULL without error.

Connects with SUPABASE_DATABASE_URL (review_iq_app) for the app-side assertions and
SUPABASE_DIRECT_URL (superuser) for seeding/cleanup, same split as test_leads_rls.py. Every
seeded org is deleted in the fixture teardown (the member row cascades).

Marked 'integration' -- skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_last_seen_functions.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[2] / ".env")

from app.core.storage_pg import get_last_seen_pg, touch_last_seen_pg  # noqa: E402


def _superuser_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"], connect_timeout=15)


def _super_exec(sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def member() -> Iterator[str]:
    """A fresh org with one member row; returns the user_id. Org is deleted afterwards."""
    user_id = str(uuid.uuid4())
    slug = f"lastseen-{user_id[:8]}"
    org_id = _super_exec(
        "INSERT INTO public.organizations (name, slug) VALUES (%s, %s) RETURNING id",
        ("Last Seen Test", slug),
    )[0][0]
    _super_exec(
        "INSERT INTO public.organization_members (org_id, user_id, role) VALUES (%s, %s, 'owner')",
        (org_id, user_id),
    )
    yield user_id
    _super_exec("DELETE FROM public.organizations WHERE id = %s", (org_id,))


@pytest.mark.integration
class TestLastSeenFunctions:
    def test_never_seen_reads_null(self, member: str) -> None:
        assert get_last_seen_pg(member) is None

    def test_touch_sets_and_returns_the_stored_value(self, member: str) -> None:
        returned = touch_last_seen_pg(member)
        assert isinstance(returned, datetime)
        stored = _super_exec(
            "SELECT last_seen_at FROM public.organization_members WHERE user_id = %s", (member,)
        )[0][0]
        assert stored == returned
        assert get_last_seen_pg(member) == returned

    def test_touch_is_debounced_inside_60_seconds(self, member: str) -> None:
        first = touch_last_seen_pg(member)
        second = touch_last_seen_pg(member)
        assert first is not None
        assert second == first  # no write: the stored value was under 60s old

    def test_touch_writes_again_once_the_stored_value_is_older_than_60_seconds(
        self, member: str
    ) -> None:
        first = touch_last_seen_pg(member)
        _super_exec(
            "UPDATE public.organization_members SET last_seen_at = now() - interval '61 seconds' "
            "WHERE user_id = %s",
            (member,),
        )
        second = touch_last_seen_pg(member)
        assert first is not None
        assert second is not None
        assert second > first

    def test_unknown_user_gets_null_and_nothing_raises(self) -> None:
        stranger = str(uuid.uuid4())
        assert get_last_seen_pg(stranger) is None
        assert touch_last_seen_pg(stranger) is None

    def test_touch_changes_only_the_callers_row(self, member: str) -> None:
        other = str(uuid.uuid4())
        org_id = _super_exec(
            "INSERT INTO public.organizations (name, slug) VALUES (%s, %s) RETURNING id",
            ("Last Seen Other", f"lastseen-{other[:8]}"),
        )[0][0]
        try:
            _super_exec(
                "INSERT INTO public.organization_members (org_id, user_id, role) "
                "VALUES (%s, %s, 'owner')",
                (org_id, other),
            )
            touch_last_seen_pg(member)
            assert get_last_seen_pg(other) is None
        finally:
            _super_exec("DELETE FROM public.organizations WHERE id = %s", (org_id,))

    def test_app_role_has_no_direct_table_access(self, member: str) -> None:
        conn = psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"], connect_timeout=15)
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("SELECT last_seen_at FROM public.organization_members")
        finally:
            conn.rollback()
            conn.close()
