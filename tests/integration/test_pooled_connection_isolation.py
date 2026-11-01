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


@pytest.fixture
def two_connection_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("DB_POOL_MAXCONN", "2")
    db_pool.close_all_pools()
    yield os.environ["SUPABASE_DATABASE_URL"]
    db_pool.close_all_pools()


def test_interleaved_open_transactions_across_orgs_never_cross(
    two_connection_pool: str, two_orgs: tuple[str, str, str, str]
) -> None:
    """Two threads hold open transactions for different orgs at the same instant, on two
    different physical connections, then return them and borrow again. Every read, at every
    stage, must show only that thread's own org; across rounds the pool must have handed at
    least one physical connection to both orgs (otherwise the interleaving proved nothing)."""
    import threading

    org_a, org_b, ext_a, ext_b = two_orgs
    rounds = 25
    barrier = threading.Barrier(2, timeout=30)
    seen: dict[str, list[set[str]]] = {"a": [], "b": []}
    physical: dict[str, list[int]] = {"a": [], "b": []}
    errors: list[BaseException] = []

    # Borrow order and release order vary per round (all four combinations), so whether the pool
    # hands back connections LIFO or FIFO, some round gives org A the connection org B just used.
    borrowed = [threading.Event() for _ in range(rounds)]
    released = [threading.Event() for _ in range(rounds)]

    def worker(name: str, org: str) -> None:
        try:
            for r in range(rounds):
                if name != ("a" if r % 2 == 0 else "b"):
                    assert borrowed[r].wait(30)
                conn = db_pool.db_connect(two_connection_pool)
                borrowed[r].set()
                cur = conn.cursor()
                _set_tenant(cur, org)
                physical[name].append(id(conn._conn))
                seen[name].append(_visible_extractions(cur))
                barrier.wait()  # both transactions are open on separate connections here
                seen[name].append(_visible_extractions(cur))  # still only my org, mid-overlap
                barrier.wait()
                if name != ("a" if (r // 2) % 2 == 0 else "b"):
                    assert released[r].wait(30)
                conn.close()  # returned mid-transaction
                released[r].set()
                barrier.wait()  # both released before either borrows again
        except BaseException as exc:  # noqa: BLE001 -- surface any thread failure to the test
            errors.append(exc)
            barrier.abort()

    threads = [
        threading.Thread(target=worker, args=("a", org_a)),
        threading.Thread(target=worker, args=("b", org_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, errors
    assert len(seen["a"]) == len(seen["b"]) == 2 * rounds
    assert all(s == {ext_a} for s in seen["a"])
    assert all(s == {ext_b} for s in seen["b"])
    assert set(physical["a"]) & set(physical["b"]), "no physical connection served both orgs"


def test_randomized_borrow_sequence_never_sees_a_foreign_org(
    one_connection_pool: str, two_orgs: tuple[str, str, str, str]
) -> None:
    import random

    org_a, org_b, ext_a, ext_b = two_orgs
    rng = random.Random(42)  # noqa: S311 -- deterministic test sequence, not security
    expected = {"a": {ext_a}, "b": {ext_b}, "none": set()}
    for _ in range(80):
        who = rng.choice(["a", "b", "none"])
        conn = db_pool.db_connect(one_connection_pool)
        cur = conn.cursor()
        if who != "none":
            _set_tenant(cur, org_a if who == "a" else org_b)
        assert _visible_extractions(cur) == expected[who], who
        if rng.random() < 0.5:
            conn.close()  # returned mid-transaction, no commit/rollback by the caller
        else:
            conn.commit()
            conn.close()


def test_failed_statement_does_not_poison_or_leak_into_the_next_borrow(
    one_connection_pool: str, two_orgs: tuple[str, str, str, str]
) -> None:
    org_a, org_b, _, ext_b = two_orgs
    with pytest.raises(psycopg2.Error):
        with db_pool.pooled_connection(one_connection_pool) as conn:
            cur = conn.cursor()
            _set_tenant(cur, org_a)
            cur.execute("SELECT 1/0")  # leaves the txn in the aborted state
    with db_pool.pooled_connection(one_connection_pool) as conn:
        cur = conn.cursor()  # InFailedSqlTransaction here would mean the abort was carried over
        _set_tenant(cur, org_b)
        assert _visible_extractions(cur) == {ext_b}


def test_cross_org_write_is_rejected_on_a_reused_connection(
    one_connection_pool: str, two_orgs: tuple[str, str, str, str]
) -> None:
    org_a, org_b, _, _ = two_orgs
    marker = f"pool_xorg_{uuid.uuid4().hex[:8]}"
    with db_pool.pooled_connection(one_connection_pool) as conn:  # warm the one connection as A
        _set_tenant(conn.cursor(), org_a)
    with pytest.raises(psycopg2.Error):
        with db_pool.pooled_connection(one_connection_pool) as conn:
            cur = conn.cursor()
            _set_tenant(cur, org_b)  # now B, on A's old physical connection
            cur.execute(
                "INSERT INTO public.extractions "
                "(id, org_id, input_hash, extraction, model, prompt_version, schema_version) "
                "VALUES (%s, %s, %s, '{}'::jsonb, 'm', 'v1', 'v1')",
                (str(uuid.uuid4()), org_a, marker),  # B writing a row owned by A
            )
            conn.commit()
    sup = _super()
    try:
        cur = sup.cursor()
        cur.execute("SELECT count(*) FROM public.extractions WHERE input_hash = %s", (marker,))
        assert cur.fetchone()[0] == 0
    finally:
        sup.close()
