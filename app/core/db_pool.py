"""Process-local Postgres connection pool -- the ONLY place app code may open a connection.

Why: every request used to open a brand-new psycopg2 connection (TCP + TLS + pooler auth)
before its first query; a healthy /health cost a constant ~1.77 s of that alone. Connections
are now reused across calls within one Cloud Run instance.

SECURITY INVARIANTS (a pooled connection outlives the request that borrowed it, so anything
left on it is visible to the next borrower -- possibly another tenant):

  I1. Tenant scope is transaction-scoped only. Call sites use `SET LOCAL ROLE` / `SET LOCAL
      "app.current_org_id"` (storage_pg._set_tenant and its copies); both vanish at COMMIT or
      ROLLBACK. Session-level SET / set_config(..., false) / PREPARE are forbidden -- they are
      also unsafe behind the transaction-mode pooler (port 6543). The CI guard
      scripts/check_undocumented_pg_connects.py fails on them.
  I2. Release ALWAYS rolls back first. A connection whose rollback fails, that is closed, or
      that is not idle-in-no-transaction afterwards is closed and dropped, never reused.
  I3. Total open connections per (process, DSN) never exceed maxconn (default 5): 3 Cloud Run
      instances x 5 = 15 client connections, below the ~60 pooled-client limit in
      ops/runbooks/connection-modes.md. A borrower waits at most `wait_s`, then gets
      PoolExhaustedError (mapped to HTTP 503 + Retry-After) -- it never hangs.
  I4. A connection idle longer than _VALIDATE_AFTER_IDLE_S is checked with SELECT 1 before it
      is handed out (Cloud Run freezes CPU between requests; the pooler may have dropped the
      socket). A failed check discards it and opens a fresh one. Connections older than
      _MAX_AGE_S are recycled so a rotated DB password / pooler restart is picked up.
  I5. Fork safe: a pool created in another PID is abandoned (never reused, sockets not closed
      from the child).

NOT done on purpose: no replay of failed statements (writes are not idempotent), no DISCARD
ALL (meaningless against a transaction-mode pooler, where our session != a backend session).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg2
import psycopg2.extensions
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_MAXCONN = 5
_DEFAULT_WAIT_S = 5.0
_VALIDATE_AFTER_IDLE_S = 15.0
_MAX_AGE_S = 600.0
# Kept in code (not the DSN) so every pooled connection carries them; sslmode etc. stay in the DSN.
_CONNECT_KWARGS: dict[str, Any] = {
    "connect_timeout": 10,
    "application_name": "review-iq",
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


class PoolExhaustedError(RuntimeError):
    """No connection became free within the bounded wait. Callers should answer 503."""


@dataclass
class _Entry:
    conn: Any
    created: float
    last_used: float


class _Pool:
    def __init__(self, dsn: str, maxconn: int, wait_s: float) -> None:
        self.dsn = dsn
        self.label = hashlib.sha256(dsn.encode()).hexdigest()[:8]  # never log the DSN
        self.maxconn = maxconn
        self.wait_s = wait_s
        self.pid = os.getpid()
        self.cond = threading.Condition()
        self.idle: list[_Entry] = []  # LIFO: hottest connection first
        self.in_use = 0  # slots held, including ones still connecting

    def stats(self) -> dict[str, int | str]:
        with self.cond:  # Condition's lock is re-entrant, so acquire() may call this too
            return {
                "pool": self.label,
                "in_use": self.in_use,
                "idle": len(self.idle),
                "maxconn": self.maxconn,
            }

    def acquire(self) -> _Entry:
        deadline = time.monotonic() + self.wait_s
        with self.cond:
            while True:
                if self.idle:
                    entry: _Entry | None = self.idle.pop()
                    break
                if self.in_use < self.maxconn:  # idle is empty => total open == in_use
                    entry = None
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning("db_pool.exhausted", wait_s=self.wait_s, **self.stats())
                    raise PoolExhaustedError(
                        f"database connection pool exhausted (maxconn={self.maxconn})"
                    )
                self.cond.wait(remaining)
            self.in_use += 1
        try:  # network I/O happens outside the lock
            if entry is not None and not self._healthy(entry):
                _close_quietly(entry.conn)
                entry = None
            if entry is None:
                now = time.monotonic()
                entry = _Entry(psycopg2.connect(self.dsn, **_CONNECT_KWARGS), now, now)
        except BaseException:
            self._free_slot()
            raise
        return entry

    @staticmethod
    def _healthy(entry: _Entry) -> bool:
        now = time.monotonic()
        if entry.conn.closed or now - entry.created > _MAX_AGE_S:
            return False
        if now - entry.last_used <= _VALIDATE_AFTER_IDLE_S:
            return True
        try:
            cur = entry.conn.cursor()
            cur.execute("SELECT 1")
            entry.conn.rollback()
        except Exception:  # noqa: BLE001 -- any failure means the socket is not trustworthy
            log.info("db_pool.stale_connection_dropped")
            return False
        return True

    def release(self, entry: _Entry, *, discard: bool = False) -> None:
        reusable = not discard and self.pid == os.getpid()
        if reusable:
            try:
                if not entry.conn.closed:
                    entry.conn.rollback()  # I2: ends any open txn, clearing SET LOCAL state
                    if entry.conn.autocommit:
                        entry.conn.autocommit = False
                status = None if entry.conn.closed else entry.conn.info.transaction_status
                reusable = status == psycopg2.extensions.TRANSACTION_STATUS_IDLE
                if time.monotonic() - entry.created > _MAX_AGE_S:
                    reusable = False
            except Exception:  # noqa: BLE001 -- a failing rollback means a dead/dirty connection
                reusable = False
        if reusable:
            entry.last_used = time.monotonic()
            with self.cond:
                self.idle.append(entry)
        else:
            _close_quietly(entry.conn)
        self._free_slot()

    def _free_slot(self) -> None:
        with self.cond:
            self.in_use -= 1
            self.cond.notify()

    def close_idle(self) -> None:
        with self.cond:
            entries, self.idle = self.idle, []
        for e in entries:
            _close_quietly(e.conn)


def _close_quietly(conn: Any) -> None:
    with contextlib.suppress(Exception):  # best effort on an already-broken connection
        conn.close()


class PooledConnection:
    """Drop-in for the psycopg2 connection the old `_db_connect()` factories returned.

    Everything is delegated to the real connection except `close()`, which returns it to the
    pool (rolling back first), so existing `try: ... finally: conn.close()` call sites need
    no change. close() is idempotent; a proxy garbage-collected without close() discards the
    connection and logs db_pool.leaked_connection instead of silently shrinking the pool.
    """

    __slots__ = ("_conn", "_entry", "_pool", "_released")

    def __init__(self, pool: _Pool, entry: _Entry) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_entry", entry)
        object.__setattr__(self, "_conn", entry.conn)
        object.__setattr__(self, "_released", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._conn, name, value)  # e.g. conn.autocommit = False

    def __enter__(self) -> PooledConnection:  # psycopg2 semantics: txn scope, not close
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

    def close(self) -> None:
        if self._released:
            return
        object.__setattr__(self, "_released", True)
        self._pool.release(self._entry)

    def __del__(self) -> None:
        try:
            if not self._released:
                object.__setattr__(self, "_released", True)
                log.warning("db_pool.leaked_connection", **self._pool.stats())
                self._pool.release(self._entry, discard=True)
        except Exception:  # noqa: BLE001, S110 -- interpreter teardown
            pass


_pools: dict[str, _Pool] = {}
_pools_lock = threading.Lock()


def _get_pool(dsn: str) -> _Pool:
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is not None and pool.pid != os.getpid():  # I5: inherited across fork
            pool = None
        if pool is None:
            pool = _Pool(
                dsn,
                maxconn=int(os.environ.get("DB_POOL_MAXCONN", _DEFAULT_MAXCONN)),
                wait_s=float(os.environ.get("DB_POOL_WAIT_SECONDS", _DEFAULT_WAIT_S)),
            )
            _pools[dsn] = pool
        return pool


def db_connect(dsn: str) -> PooledConnection:
    """Borrow a connection; the caller MUST close() it (or use `pooled_connection`).

    `dsn` is required (no settings fallback): a caller that passes an unset admin DSN must not
    silently be handed the public app role's pool instead (rule 98a, fail closed)."""
    pool = _get_pool(dsn)
    return PooledConnection(pool, pool.acquire())


@contextmanager
def pooled_connection(dsn: str) -> Iterator[PooledConnection]:
    """`with pooled_connection() as conn:` -- always rolled back + returned on exit."""
    conn = db_connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


def pool_stats() -> list[dict[str, int | str]]:
    with _pools_lock:
        pools = list(_pools.values())
    return [p.stats() for p in pools]


def close_all_pools() -> None:
    """Close idle connections and forget every pool (shutdown hook / test isolation)."""
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for p in pools:
        p.close_idle()
