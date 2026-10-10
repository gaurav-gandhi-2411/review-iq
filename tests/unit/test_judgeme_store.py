"""PG backend contract tests with a recording fake connection (no database is touched).

These prove SQL shape, tenant scoping and parameter binding only; row-level security itself is
exercised by an integration test that needs a real database (not run here).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg2.errors
import pytest
from app.core.ingestion import judgeme_store as store
from app.core.ingestion.judgeme_source import map_item
from app.core.ingestion.judgeme_sync import StateRow

from tests.support.judgeme_fake import make_review

ORG = "11111111-1111-1111-1111-111111111111"
INST = "22222222-2222-2222-2222-222222222222"


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.rowcount = 3
        self.description: list[tuple[str]] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.conn.log.append((sql, params))
        if self.conn.raise_on and self.conn.raise_on in sql:
            raise self.conn.exc  # type: ignore[misc]

    def executemany(self, sql: str, seq: Any) -> None:
        self.conn.log.append((sql, list(seq)))

    def fetchone(self) -> Any:
        return self.conn.rows[0] if self.conn.rows else None

    def fetchall(self) -> Any:
        return self.conn.rows


class FakeConn:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or []
        self.log: list[tuple[str, Any]] = []
        self.commits = self.rollbacks = 0
        self.closed = False
        self.raise_on = ""
        self.exc: Exception | None = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def patch(monkeypatch: pytest.MonkeyPatch, conn: FakeConn) -> FakeConn:
    monkeypatch.setattr(store, "_db_connect", lambda: conn)
    return conn


def test_state_roundtrip_is_tenant_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    d = datetime(2026, 9, 1, tzinfo=UTC)
    conn = patch(monkeypatch, FakeConn([("7", "ch", "sha256:ab", "active", 1, d, d)]))
    out = store.load_state_pg(ORG, INST)
    assert out == {"7": StateRow("7", "ch", "sha256:ab", "active", 1, d, d)}
    assert conn.log[0][0] == "SET LOCAL ROLE authenticated"
    assert conn.log[1][1] == (ORG,)
    assert "org_id = %s AND installation_id = %s" in conn.log[2][0] and conn.log[2][1] == (
        ORG,
        INST,
    )

    conn2 = patch(monkeypatch, FakeConn())
    store.save_state_pg(ORG, INST, list(out.values()))
    sql, params = conn2.log[2]
    assert "ON CONFLICT (installation_id, review_id) DO UPDATE" in sql
    assert params[0][:3] == (INST, "7", ORG) and conn2.commits == 1


def test_delete_extractions_binds_org_and_hash_array(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = patch(monkeypatch, FakeConn())
    assert store.delete_extractions_by_hash_pg(ORG, ["sha256:a", "sha256:b"]) == 3
    sql, params = conn.log[2]
    assert "WHERE org_id = %s AND input_hash = ANY(%s)" in sql
    assert params == (ORG, ["sha256:a", "sha256:b"])


def test_get_installation_never_selects_token_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = patch(monkeypatch, FakeConn())
    store.get_installation_pg(ORG)
    assert "token_enc" not in conn.log[2][0]
    conn2 = patch(monkeypatch, FakeConn())
    store.get_installation_pg(ORG, INST, with_token=True)
    assert ", token_enc FROM" in conn2.log[2][0] and "AND id = %s" in conn2.log[2][0]


def test_privileged_writes_go_through_definer_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = patch(monkeypatch, FakeConn([("33333333-3333-3333-3333-333333333333",)]))
    assert store.upsert_installation_pg(ORG, "a.myshopify.com", "ct", "header") == (
        "33333333-3333-3333-3333-333333333333"
    )
    assert "public.upsert_judgeme_installation" in conn.log[0][0]
    conn = patch(monkeypatch, FakeConn())
    store.revoke_installation_pg(INST, ORG, "token_rejected")
    assert conn.log[0] == (
        "SELECT public.revoke_judgeme_installation(%s, %s, %s)",
        (INST, ORG, "token_rejected"),
    )
    conn = patch(monkeypatch, FakeConn())
    store.record_sync_pg(
        INST,
        ORG,
        status="ok",
        error=None,
        watermark=None,
        last_full_scan_at=None,
        order_violation=False,
        summary={"new": 1},
        next_attempt_at=None,
        failed=False,
    )
    assert "public.record_judgeme_sync" in conn.log[0][0] and '{"new": 1}' in conn.log[0][1]


def test_shop_connected_elsewhere_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = patch(monkeypatch, FakeConn())
    conn.raise_on = "upsert_judgeme_installation"
    conn.exc = psycopg2.errors.UniqueViolation("shop_connected_elsewhere")
    with pytest.raises(store.ShopConnectedElsewhere):
        store.upsert_installation_pg(ORG, "a.myshopify.com", "ct", "header")
    assert conn.closed and conn.rollbacks >= 1
    conn.exc = psycopg2.errors.UniqueViolation("duplicate key something else")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        store.upsert_installation_pg(ORG, "a.myshopify.com", "ct", "header")


def test_list_due_returns_ids_only(monkeypatch: pytest.MonkeyPatch) -> None:
    patch(monkeypatch, FakeConn([(INST, ORG)]))
    assert store.list_due_installations_pg(360) == [(INST, ORG)]


async def test_stage_rows_uses_the_csv_queue_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(store, "create_batch_job_pg", lambda *a: calls.append(("create", a)))
    monkeypatch.setattr(store, "enqueue_batch_job_rows_pg", lambda *a: calls.append(("enqueue", a)))
    monkeypatch.setattr(store, "update_batch_job_pg", lambda *a, **k: calls.append(("update", k)))
    rows = [map_item(make_review(1)), map_item(make_review(2, body="Second"))]
    job_id = await store.PgBackend(ORG, INST).stage(rows)  # type: ignore[arg-type]
    assert [c[0] for c in calls] == ["create", "enqueue", "update"]
    assert calls[0][1][:3] == (ORG, job_id, 2)
    _, (org, jid, texts, products, dates) = calls[1]
    assert (org, jid) == (ORG, job_id) and len(texts) == 2 and products == ["Product 111"] * 2
    assert all(isinstance(d, datetime) for d in dates)
    assert calls[2][1] == {"status": "processing"}
    blob = repr(calls)
    assert (
        "@example.com" not in blob and "Buyer Name" not in blob
    )  # no reviewer PII reaches the queue
