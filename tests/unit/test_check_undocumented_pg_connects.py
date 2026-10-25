"""Hostile-input tests for scripts/check_undocumented_pg_connects.py.

This guard had no tests at all. It is the control that decides whether a psycopg2 call site is
tenant-scoped, so each test below feeds it a deliberately unscoped function and asserts it is
reported -- plus the shapes it is documented NOT to see, pinned so the gap stays visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import scripts.check_undocumented_pg_connects as guard


@pytest.fixture
def app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard, "APP_DIR", app)
    return app


def _run(app: Path, source: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    (app / "mod.py").write_text(source, encoding="utf-8")
    code = guard.main()
    captured = capsys.readouterr()
    return code, captured.out + captured.err


UNSCOPED_BODY = """
    conn = {connect}
    cur = conn.cursor()
    cur.execute("SELECT * FROM extractions")
    return cur.fetchall()
"""


def test_real_repo_passes() -> None:
    assert guard.main() == 0


def test_unscoped_direct_connect_is_reported(app_dir, capsys) -> None:
    src = "import psycopg2\n\ndef leak(dsn):" + UNSCOPED_BODY.format(
        connect="psycopg2.connect(dsn)"
    )
    code, out = _run(app_dir, src, capsys)
    assert code == 1
    assert "leak()" in out


def test_scoped_function_passes(app_dir, capsys) -> None:
    src = (
        "from app.core.db_pool import db_connect\n\ndef ok(dsn, org):\n"
        "    conn = db_connect(dsn)\n"
        "    _set_tenant(conn, org)\n"
        "    cur = conn.cursor()\n"
        "    cur.execute('SELECT 1')\n"
    )
    assert _run(app_dir, src, capsys)[0] == 0


def test_module_alias_connect_is_reported(app_dir, capsys) -> None:
    # `import psycopg2 as pg` -- the receiver name no longer contains "psycopg2".
    src = "import psycopg2 as pg\n\ndef leak(dsn):" + UNSCOPED_BODY.format(
        connect="pg.connect(dsn)"
    )
    code, out = _run(app_dir, src, capsys)
    assert code == 1
    assert "leak()" in out


def test_from_import_connect_is_reported(app_dir, capsys) -> None:
    src = "from psycopg2 import connect\n\ndef leak(dsn):" + UNSCOPED_BODY.format(
        connect="connect(dsn)"
    )
    code, out = _run(app_dir, src, capsys)
    assert code == 1
    assert "leak()" in out


def test_from_import_renamed_connect_is_reported(app_dir, capsys) -> None:
    src = "from psycopg2 import connect as dial\n\ndef leak(dsn):" + UNSCOPED_BODY.format(
        connect="dial(dsn)"
    )
    assert _run(app_dir, src, capsys)[0] == 1


def test_unrelated_connect_is_not_flagged(app_dir, capsys) -> None:
    # sqlite3.connect is out of scope for a psycopg2 tenant guard.
    src = "import sqlite3\n\ndef local(path):" + UNSCOPED_BODY.format(
        connect="sqlite3.connect(path)"
    )
    assert _run(app_dir, src, capsys)[0] == 0


def test_empty_app_dir_fails_instead_of_ok(app_dir, capsys) -> None:
    assert guard.main() == 1
    assert "refusing to pass a scan of nothing" in capsys.readouterr().out


def test_pooled_direct_borrow_unscoped_is_reported(app_dir, capsys) -> None:
    # S23: db_connect()/pooled_connection() must count as "opening a connection", otherwise
    # converting a call site to the pool would make it invisible to this guard.
    src = (
        "from app.core.db_pool import pooled_connection\n\n"
        "def leak():\n"
        "    with pooled_connection() as conn:\n"
        "        cur = conn.cursor()\n"
        "        cur.execute('SELECT * FROM extractions')\n"
    )
    code, out = _run(app_dir, src, capsys)
    assert code == 1
    assert "leak()" in out


def test_pooled_factory_in_another_module_is_still_followed(app_dir, capsys) -> None:
    # The cross-file factory shape (rule 85a) with the NEW factory body: a `_db_connect` that
    # wraps db_connect() lives in one module, its unscoped caller in another.
    (app_dir / "factory.py").write_text(
        "from app.core.db_pool import db_connect\n\n"
        "def _db_connect():\n    return db_connect('dsn')\n",
        encoding="utf-8",
    )
    src = "from factory import _db_connect\n\ndef leak():" + UNSCOPED_BODY.format(
        connect="_db_connect()"
    )
    code, out = _run(app_dir, src, capsys)
    assert code == 1
    assert "leak()" in out


def test_pooled_borrow_with_set_tenant_passes(app_dir, capsys) -> None:
    src = (
        "from app.core.db_pool import db_connect\n\n"
        "def ok(org):\n"
        "    conn = db_connect()\n"
        "    cur = conn.cursor()\n"
        "    _set_tenant(cur, org)\n"
        "    cur.execute('SELECT 1')\n"
    )
    assert _run(app_dir, src, capsys)[0] == 0


def test_pool_module_itself_is_not_scanned(app_dir, capsys) -> None:
    core = app_dir / "core"
    core.mkdir()
    (core / "db_pool.py").write_text(
        "import psycopg2\n\ndef x(dsn):" + UNSCOPED_BODY.format(connect="psycopg2.connect(dsn)"),
        encoding="utf-8",
    )
    (app_dir / "mod.py").write_text("X = 1\n", encoding="utf-8")
    assert guard.main() == 0


@pytest.mark.parametrize(
    "src",
    [
        "import psycopg2\n\ndef f(dsn):\n    return psycopg2.connect(dsn)\n",
        "import psycopg2 as pg\n\ndef f(dsn):\n    return pg.connect(dsn)\n",
        "from psycopg2 import connect\n\ndef f(dsn):\n    return connect(dsn)\n",
        # a bare reference, not a call (this is how ingest_worker used to open its connection)
        "import psycopg2\n\nasync def f(dsn):\n    return await to_thread(psycopg2.connect, dsn)\n",
    ],
)
def test_raw_connect_is_banned_even_without_a_query(app_dir, capsys, src) -> None:
    code, out = _run(app_dir, src, capsys)
    assert code == 1
    assert "raw psycopg2.connect" in out


def test_raw_connect_is_banned_in_file_allowlisted_admin_module(app_dir, capsys) -> None:
    # admin.py is exempt from tenant scoping (FILE_ALLOWLIST) but not from pooling hygiene.
    (app_dir / "api").mkdir()
    (app_dir / "api" / "admin.py").write_text(
        "import psycopg2\n\ndef c(dsn):\n    return psycopg2.connect(dsn)\n", encoding="utf-8"
    )
    assert guard.main() == 1
    assert "app/api/admin.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    "sql",
    [
        'SET "app.current_org_id" = %s',
        "SET ROLE authenticated",
        "SET SESSION statement_timeout = 5000",
        "SET search_path TO public",
        "SELECT set_config('app.current_org_id', %s, false)",
        "PREPARE q AS SELECT 1",
    ],
)
def test_session_scoped_sql_is_banned(app_dir, capsys, sql) -> None:
    code, out = _run(app_dir, f"def f(cur):\n    cur.execute({sql!r})\n", capsys)
    assert code == 1
    assert "session-scoped SQL" in out


@pytest.mark.parametrize(
    "sql",
    [
        'SET LOCAL "app.current_org_id" = %s',
        "SET LOCAL ROLE authenticated",
        "SELECT set_config('app.current_org_id', %s, true)",
        "UPDATE t SET a = 1",
        "Set RLS context for the current transaction.",
    ],
)
def test_transaction_scoped_or_unrelated_sql_is_allowed(app_dir, capsys, sql) -> None:
    code, _ = _run(app_dir, f"def f(cur):\n    cur.execute({sql!r})\n", capsys)
    assert code == 0


def test_KNOWN_GAP_set_tenant_after_the_query_still_passes(app_dir, capsys) -> None:
    """Pins a documented limitation (see the module docstring's SURFACE paragraph): the guard
    is structural, so `_set_tenant` called AFTER the unscoped query satisfies it. UNVERIFIED
    behaviourally here -- tests/integration/test_adversarial_cross_tenant.py is the real check.
    If this starts failing because the guard got smarter, delete this test."""
    src = (
        "from app.core.db_pool import db_connect\n\ndef late(dsn, org):\n"
        "    conn = db_connect(dsn)\n"
        "    cur = conn.cursor()\n"
        "    cur.execute('SELECT * FROM extractions')\n"
        "    _set_tenant(conn, org)\n"
    )
    assert _run(app_dir, src, capsys)[0] == 0


def test_KNOWN_GAP_connection_passed_in_as_parameter_is_not_seen(app_dir, capsys) -> None:
    """Pins a documented limitation: a function handed an open connection never 'opens' one, so
    it is out of scope even though it queries without _set_tenant."""
    src = "def leak(conn):\n    cur = conn.cursor()\n    cur.execute('SELECT * FROM extractions')\n"
    assert _run(app_dir, src, capsys)[0] == 0
