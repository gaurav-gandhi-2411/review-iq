"""Pooled-connection reuse must never carry one tenant's scope into the next borrow (S23).

The unit tests (tests/unit/test_db_pool.py) prove this against a fake; this proves it against
a real Postgres with every migration applied (the pre-cutover-verification job's throwaway
container). A pool of ONE connection forces every borrow to reuse the same physical connection,
so any state a request leaves behind -- the `SET LOCAL ROLE authenticated`, the
`app.current_org_id` GUC, an uncommitted write -- is visible to the next borrower if the pool
fails to clean up. Uses review_iq_app's own credential (SUPABASE_DATABASE_URL) through
app.core.db_pool and the superuser (TEST_DB_*) only to seed and clean up.

Marked 'integration' -- skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_pooled_connection_isolation.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

from tests.integration._superuser_db_params import superuser_db_params

load_dotenv(Path(__file__).parents[2] / ".env")

from app.core import db_pool  # noqa: E402
from app.core.storage_pg import _set_tenant  # noqa: E402

pytestmark = pytest.mark.integration


def _super() -> psycopg2.extensions.connection:
    return psycopg2.connect(**superuser_db_params())


@pytest.fixture
def one_connection_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("DB_POOL_MAXCONN", "1")
    db_pool.close_all_pools()
    yield os.environ["SUPABASE_DATABASE_URL"]
    db_pool.close_all_pools()


@pytest.fixture(scope="module")
def two_orgs() -> Iterator[tuple[str, str, str, str]]:
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    ext_a, ext_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = _super()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES "
            "(%s, 'Pool A', %s), (%s, 'Pool B', %s)",
            (org_a, f"pool-a-{org_a[:8]}", org_b, f"pool-b-{org_b[:8]}"),
        )
        cur.execute(
            "INSERT INTO public.extractions "
            "(id, org_id, input_hash, extraction, model, prompt_version, schema_version) VALUES "
            "(%s, %s, 'pool_a', '{}'::jsonb, 'm', 'v1', 'v1'), "
            "(%s, %s, 'pool_b', '{}'::jsonb, 'm', 'v1', 'v1')",
            (ext_a, org_a, ext_b, org_b),
        )
        conn.commit()
    finally:
        conn.close()
    yield org_a, org_b, ext_a, ext_b
    conn = _super()
    try:
        conn.cursor().execute(
            "DELETE FROM public.organizations WHERE id IN (%s, %s)", (org_a, org_b)
        )
        conn.commit()
    finally:
        conn.close()


def _visible_extractions(cur: object) -> set[str]:
    cur.execute("SELECT id::text FROM public.extractions")  # type: ignore[attr-defined]
    return {r[0] for r in cur.fetchall()}  # type: ignore[attr-defined]


def test_tenant_scope_does_not_leak_to_next_borrower(
    one_connection_pool: str, two_orgs: tuple[str, str, str, str]
) -> None:
    org_a, org_b, ext_a, ext_b = two_orgs

    a = db_pool.db_connect(one_connection_pool)
    first = a._conn
    cur = a.cursor()
    _set_tenant(cur, org_a)
    assert _visible_extractions(cur) == {ext_a}
    a.close()  # returned mid-transaction: no commit, no rollback by the caller

    nxt = db_pool.db_connect(one_connection_pool)
    assert nxt._conn is first, "pool of 1 must hand back the same physical connection"
    cur = nxt.cursor()
    cur.execute("SELECT current_user, current_setting('app.current_org_id', true)")
    user, org = cur.fetchone()
    assert user != "authenticated", "SET LOCAL ROLE leaked across borrows"
    assert org in (None, ""), f"app.current_org_id leaked across borrows: {org!r}"
    nxt.close()

    b = db_pool.db_connect(one_connection_pool)
    cur = b.cursor()
    _set_tenant(cur, org_b)
    assert _visible_extractions(cur) == {ext_b}  # tenant B sees only B, never A's leftover scope
    b.close()


def test_uncommitted_write_from_a_failed_request_is_rolled_back(
    one_connection_pool: str, two_orgs: tuple[str, str, str, str]
) -> None:
    org_a, _, _, _ = two_orgs
    marker = f"pool_dirty_{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError, match="request failed"):
        with db_pool.pooled_connection(one_connection_pool) as conn:
            cur = conn.cursor()
            _set_tenant(cur, org_a)
            cur.execute(
                "INSERT INTO public.extractions "
                "(id, org_id, input_hash, extraction, model, prompt_version, schema_version) "
                "VALUES (%s, %s, %s, '{}'::jsonb, 'm', 'v1', 'v1')",
                (str(uuid.uuid4()), org_a, marker),
            )
            raise RuntimeError("request failed")
    with db_pool.pooled_connection(one_connection_pool) as conn:
        conn.commit()  # a dirty reuse would COMMIT the leftover INSERT right here
    sup = _super()
    try:
        cur = sup.cursor()
        cur.execute("SELECT count(*) FROM public.extractions WHERE input_hash = %s", (marker,))
        assert cur.fetchone()[0] == 0
    finally:
        sup.close()
