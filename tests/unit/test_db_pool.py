"""Hostile tests for app/core/db_pool.py (S23).

A pooled connection outlives the request that borrowed it, so each test below tries to make
the NEXT borrower see something the PREVIOUS one left behind (tenant scope, an uncommitted
write, a dead socket) or to starve/hang the pool. psycopg2.connect is replaced by an offline
fake that models transaction-scoped (SET LOCAL) vs session-scoped (SET) state, so the
transaction-scoping invariant is checked against the repo's REAL `_set_tenant`. The matching
real-Postgres test is tests/integration/test_pooled_connection_isolation.py.
"""

from __future__ import annotations

import gc
import threading
import time
from typing import Any

import psycopg2
import psycopg2.extensions
import pytest
import structlog
from app.core import db_pool
from app.core.db_pool import PoolExhaustedError, db_connect, pooled_connection

DSN = "postgresql://u:pw@db.invalid:6543/postgres"
_IDLE = psycopg2.extensions.TRANSACTION_STATUS_IDLE
_INTRANS = psycopg2.extensions.TRANSACTION_STATUS_INTRANS


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self._row: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        c = self.conn
        if c.dead:
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        c.status = _INTRANS
        if sql.startswith("SET LOCAL ROLE"):
            c.local["role"] = sql.split()[-1]
        elif sql.startswith('SET LOCAL "app.current_org_id"'):
            c.local["org"] = params[0] if params else None
        elif sql.startswith('SET "app.current_org_id"'):  # session-level: the hostile shape
            c.session["org"] = params[0] if params else None
        elif sql.startswith("INSERT"):
            c.pending.append(params)
        elif sql.startswith("SELECT scope"):
            self._row = (
                c.local.get("role", c.session["role"]),
                c.local.get("org", c.session["org"]),
            )

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _Info:
    def __init__(self, conn: FakeConn) -> None:
        self._c = conn

    @property
    def transaction_status(self) -> int:
        return self._c.status


class FakeConn:
    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self.session: dict[str, Any] = {"role": "review_iq_app", "org": None}
        self.local: dict[str, Any] = {}
        self.pending: list[Any] = []
        self.status = _IDLE
        self.closed = 0
        self.autocommit = False
        self.dead = False
        self.fail_rollback = False
        self.info = _Info(self)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.db.rows.extend(self.pending)
        self._end()

    def rollback(self) -> None:
        if self.fail_rollback:
            raise psycopg2.OperationalError("rollback failed")
        self._end()

    def _end(self) -> None:
        self.pending.clear()
        self.local.clear()
        self.status = _IDLE

    def close(self) -> None:
        if not self.closed:
            self.closed = 1
            self.db.open -= 1


class FakeDB:
    def __init__(self) -> None:
        self.conns: list[FakeConn] = []
        self.rows: list[Any] = []
        self.open = 0
        self.peak = 0
        self.connect_kwargs: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def connect(self, dsn: str, **kwargs: Any) -> FakeConn:
        with self.lock:
            c = FakeConn(self)
            self.conns.append(c)
            self.open += 1
            self.peak = max(self.peak, self.open)
            self.connect_kwargs.append(kwargs)
        return c


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeDB:
    db_pool.close_all_pools()
    db = FakeDB()
    monkeypatch.setattr(db_pool.psycopg2, "connect", db.connect)
    monkeypatch.setenv("DB_POOL_MAXCONN", "5")
    monkeypatch.setenv("DB_POOL_WAIT_SECONDS", "0.3")
    yield db
    db_pool.close_all_pools()


def _scope(conn: Any) -> tuple[Any, Any]:
    cur = conn.cursor()
    cur.execute("SELECT scope")
    return cur.fetchone()


def _only_pool() -> db_pool._Pool:
    return next(iter(db_pool._pools.values()))


def test_connections_are_reused_not_reopened(fake_db: FakeDB) -> None:
    for _ in range(10):
        with pooled_connection(DSN) as conn:
            conn.cursor().execute("SELECT 1")
    assert len(fake_db.conns) == 1  # one connect() for ten borrows


def test_connect_keeps_timeout_application_name_and_keepalives(fake_db: FakeDB) -> None:
    db_connect(DSN).close()
    kw = fake_db.connect_kwargs[0]
    assert kw["connect_timeout"] == 10
    assert kw["application_name"] == "review-iq"
    assert kw["keepalives"] == 1


def test_tenant_scope_never_visible_to_next_borrower(fake_db: FakeDB) -> None:
    """I1/I2 with the repo's REAL _set_tenant: tenant A scopes, returns WITHOUT committing."""
    from app.core.storage_pg import _set_tenant

    a = db_connect(DSN)
    _set_tenant(a.cursor(), "org-A")
    assert _scope(a) == ("authenticated", "org-A")
    a.close()  # no commit, no rollback: the pool must clean up

    b = db_connect(DSN)
    assert b._conn is fake_db.conns[0]  # proves the SAME physical connection was reused
    assert _scope(b) == ("review_iq_app", None)
    b.close()


def test_tenant_scope_cleared_after_commit_too(fake_db: FakeDB) -> None:
    from app.core.storage_pg import _set_tenant

    with pooled_connection(DSN) as a:
        _set_tenant(a.cursor(), "org-A")
        a.commit()
    with pooled_connection(DSN) as b:
        assert _scope(b) == ("review_iq_app", None)


def test_KNOWN_GAP_session_level_set_survives_reuse(fake_db: FakeDB) -> None:
    """Pins what the pool deliberately does NOT do: a session-level `SET` (no LOCAL) outlives
    rollback. That is why invariant I1 forbids it and the CI guard fails on it (see
    tests/unit/test_check_undocumented_pg_connects.py); if the pool ever learns to RESET ALL,
    delete this test."""
    with pooled_connection(DSN) as a:
        a.cursor().execute('SET "app.current_org_id" = %s', ("org-A",))
    with pooled_connection(DSN) as b:
        assert _scope(b)[1] == "org-A"


def test_failed_transaction_is_rolled_back_before_reuse(fake_db: FakeDB) -> None:
    with pytest.raises(ValueError, match="boom"), pooled_connection(DSN) as conn:
        conn.cursor().execute("INSERT INTO t VALUES (%s)", ("half-done",))
        raise ValueError("boom")
    with pooled_connection(DSN) as conn2:
        assert conn2._conn is fake_db.conns[0]
        conn2.commit()
    assert fake_db.rows == []  # the uncommitted write never became visible


def test_connection_whose_rollback_fails_is_discarded_not_reused(fake_db: FakeDB) -> None:
    conn = db_connect(DSN)
    conn._conn.fail_rollback = True
    conn.close()
    assert fake_db.conns[0].closed
    with pooled_connection(DSN) as fresh:
        assert fresh._conn is fake_db.conns[1]


def test_pool_is_bounded_and_exhaustion_is_a_fast_error(fake_db: FakeDB, monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_MAXCONN", "1")
    held = db_connect(DSN)
    t0 = time.monotonic()
    with structlog.testing.capture_logs() as logs, pytest.raises(PoolExhaustedError):
        db_connect(DSN)
    assert time.monotonic() - t0 < 2.0  # bounded wait (0.3 s configured), never a hang
    warn = [e for e in logs if e["event"] == "db_pool.exhausted"]
    assert warn and warn[0]["log_level"] == "warning"
    assert warn[0]["in_use"] == 1 and warn[0]["maxconn"] == 1
    assert DSN not in str(warn)  # stats are labelled by hash, never by DSN
    held.close()
    db_connect(DSN).close()  # slot returned -> pool usable again
    assert len(fake_db.conns) == 1


def test_waiter_gets_connection_when_one_is_released(fake_db: FakeDB, monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_MAXCONN", "1")
    monkeypatch.setenv("DB_POOL_WAIT_SECONDS", "3")
    held = db_connect(DSN)
    threading.Timer(0.1, held.close).start()
    got = db_connect(DSN)  # blocks until the timer releases `held`
    got.close()
    assert len(fake_db.conns) == 1


def test_stale_connection_is_dropped_and_replaced(fake_db: FakeDB) -> None:
    """Pooler restart / idle-timeout: first conn is dead; the borrower must get a fresh one."""
    db_connect(DSN).close()
    fake_db.conns[0].dead = True
    _only_pool().idle[0].last_used -= 60  # idle past the validation threshold
    with pooled_connection(DSN) as conn:
        conn.cursor().execute("SELECT 1")  # would raise OperationalError on the dead conn
        assert conn._conn is fake_db.conns[1]
    assert fake_db.conns[0].closed


def test_recently_used_connection_is_not_revalidated(fake_db: FakeDB) -> None:
    db_connect(DSN).close()
    fake_db.conns[0].dead = True  # would fail a ping -- but it was used a moment ago
    conn = db_connect(DSN)
    assert conn._conn is fake_db.conns[0]  # trusted without a round trip; no replay either
    conn.close()


def test_expired_connection_is_recycled(fake_db: FakeDB) -> None:
    db_connect(DSN).close()
    _only_pool().idle[0].created -= db_pool._MAX_AGE_S + 1
    with pooled_connection(DSN) as conn:
        assert conn._conn is fake_db.conns[1]


def test_close_is_idempotent_and_does_not_double_free(fake_db: FakeDB) -> None:
    conn = db_connect(DSN)
    conn.close()
    conn.close()
    assert _only_pool().stats()["in_use"] == 0
    assert len(_only_pool().idle) == 1


def test_leaked_proxy_is_discarded_and_slot_recovered(fake_db: FakeDB, monkeypatch) -> None:
    monkeypatch.setenv("DB_POOL_MAXCONN", "1")
    leaked = db_connect(DSN)
    leaked._conn.cursor().execute("INSERT INTO t VALUES (%s)", ("dirty",))
    with structlog.testing.capture_logs() as logs:
        del leaked
        gc.collect()
    assert any(e["event"] == "db_pool.leaked_connection" for e in logs)
    assert fake_db.conns[0].closed  # dirty leaked connection is not reused
    with pooled_connection(DSN) as conn:  # slot was recovered (maxconn=1 would hang otherwise)
        assert conn._conn is fake_db.conns[1]


def test_pool_inherited_across_fork_is_not_reused(fake_db: FakeDB, monkeypatch) -> None:
    db_connect(DSN).close()
    monkeypatch.setattr(db_pool.os, "getpid", lambda: 999_999)
    with pooled_connection(DSN) as conn:
        assert conn._conn is fake_db.conns[1]
    assert not fake_db.conns[0].closed  # parent's socket is not closed from the "child"


def test_open_connections_never_exceed_maxconn_under_concurrency(
    fake_db: FakeDB, monkeypatch
) -> None:
    monkeypatch.setenv("DB_POOL_MAXCONN", "3")
    monkeypatch.setenv("DB_POOL_WAIT_SECONDS", "10")
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(5):
                with pooled_connection(DSN) as conn:
                    conn.cursor().execute("SELECT 1")
                    time.sleep(0.002)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert fake_db.peak <= 3
    assert _only_pool().stats()["in_use"] == 0


def test_with_connection_keeps_psycopg2_semantics(fake_db: FakeDB) -> None:
    """`with conn:` commits/rolls back but does NOT close or release (psycopg2 behaviour)."""
    conn = db_connect(DSN)
    with conn:
        conn.cursor().execute("INSERT INTO t VALUES (%s)", ("x",))
    assert fake_db.rows == [("x",)]
    assert _only_pool().stats()["in_use"] == 1
    conn.close()


def test_connect_failure_frees_the_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    db_pool.close_all_pools()
    monkeypatch.setenv("DB_POOL_MAXCONN", "1")

    def boom(dsn: str, **kw: Any) -> None:
        raise psycopg2.OperationalError("connection refused")

    monkeypatch.setattr(db_pool.psycopg2, "connect", boom)
    for _ in range(3):  # would hang/exhaust on the 2nd try if the slot leaked
        with pytest.raises(psycopg2.OperationalError):
            db_connect(DSN)
    assert _only_pool().stats()["in_use"] == 0
    db_pool.close_all_pools()


def test_pool_exhausted_handler_returns_503_with_retry_after() -> None:
    from app.main import _pool_exhausted_handler
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_exception_handler(PoolExhaustedError, _pool_exhausted_handler)

    @app.get("/x")
    def x() -> None:
        raise PoolExhaustedError("pool exhausted")

    r = TestClient(app).get("/x")
    assert r.status_code == 503
    assert r.headers["retry-after"] == "2"
    assert "pool" not in r.text.lower()  # no internals in the body
