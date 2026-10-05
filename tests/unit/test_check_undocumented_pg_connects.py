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
        "import psycopg2\n\ndef ok(dsn, org):\n"
        "    conn = psycopg2.connect(dsn)\n"
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


def test_KNOWN_GAP_set_tenant_after_the_query_still_passes(app_dir, capsys) -> None:
    """Pins a documented limitation (see the module docstring's SURFACE paragraph): the guard
    is structural, so `_set_tenant` called AFTER the unscoped query satisfies it. UNVERIFIED
    behaviourally here -- tests/integration/test_adversarial_cross_tenant.py is the real check.
    If this starts failing because the guard got smarter, delete this test."""
    src = (
        "import psycopg2\n\ndef late(dsn, org):\n"
        "    conn = psycopg2.connect(dsn)\n"
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
