"""Tests for scripts/check_migrations_no_bypassrls_grant.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scripts import check_migrations_no_bypassrls_grant as guard

REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _run(tmp_path: Path, sql: str, name: str = "20990101000001_new.sql") -> int:
    (tmp_path / name).write_text(sql, encoding="utf-8")
    return guard.main([str(tmp_path)])


def test_grant_in_migration_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, "CREATE TABLE t (a int);\nALTER ROLE review_iq_app BYPASSRLS;\n") == 1
    err = capsys.readouterr().err
    assert "20990101000001_new.sql:2" in err
    assert "review_iq_app" in err


def test_nobypassrls_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, "ALTER ROLE review_iq_app NOBYPASSRLS;\n") == 0
    assert _run(tmp_path, "ALTER ROLE review_iq_app WITH LOGIN nobypassrls;\n") == 0


def test_line_comment_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, "-- ALTER ROLE review_iq_app BYPASSRLS;\nSELECT 1;\n") == 0


def test_block_comment_passes(tmp_path: Path) -> None:
    sql = (
        "/* old\n ALTER ROLE review_iq_app BYPASSRLS;\n /* nested */ still comment */\nSELECT 1;\n"
    )
    assert _run(tmp_path, sql) == 0


def test_grant_after_block_comment_still_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, "/* c */ ALTER ROLE x BYPASSRLS; -- trailing\n") == 1


def test_multiline_statement_fails(tmp_path: Path) -> None:
    sql = "ALTER ROLE review_iq_app\n  WITH LOGIN\n  -- keep going\n  BYPASSRLS\n;\n"
    assert _run(tmp_path, sql) == 1


def test_alter_user_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, "ALTER USER review_iq_app BYPASSRLS;\n") == 1


def test_create_role_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, "CREATE ROLE evil LOGIN BYPASSRLS;\n") == 1


def test_quoted_role_and_case_fail(tmp_path: Path) -> None:
    assert _run(tmp_path, 'alter role "review_iq_app" bypassrls;\n') == 1


def test_grant_inside_do_block_fails(tmp_path: Path) -> None:
    sql = "DO $$\nBEGIN\n  ALTER ROLE review_iq_app BYPASSRLS;\nEND\n$$;\n"
    assert _run(tmp_path, sql) == 1


def test_semicolon_in_string_does_not_hide_grant(tmp_path: Path) -> None:
    assert _run(tmp_path, "ALTER ROLE x PASSWORD 'a;b' BYPASSRLS;\n") == 1


def test_execute_string_is_conservative_hit(tmp_path: Path) -> None:
    sql = "DO $$ BEGIN EXECUTE format('ALTER ROLE %I BYPASSRLS', 'x'); END $$;\n"
    assert _run(tmp_path, sql) == 1


def test_execute_string_nobypassrls_passes(tmp_path: Path) -> None:
    sql = "DO $$ BEGIN EXECUTE format('ALTER ROLE %I NOBYPASSRLS', 'x'); END $$;\n"
    assert _run(tmp_path, sql) == 0


def test_bypassrls_in_comment_on_string_passes(tmp_path: Path) -> None:
    sql = "COMMENT ON FUNCTION f() IS 'BYPASSRLS remediation; GRANT EXECUTE done';\n"
    assert _run(tmp_path, sql) == 0


def test_allowlist_is_role_and_file_specific(tmp_path: Path) -> None:
    allow = {("review_iq_admin", "ok.sql"): "test reason"}
    stmt = "ALTER ROLE review_iq_admin BYPASSRLS;\n"
    (tmp_path / "ok.sql").write_text(stmt, encoding="utf-8")
    assert guard.check(tmp_path, allow) == []
    (tmp_path / "other.sql").write_text(stmt, encoding="utf-8")
    failures = guard.check(tmp_path, allow)
    assert len(failures) == 1
    assert failures[0].startswith("other.sql:1")
    (tmp_path / "other.sql").unlink()
    (tmp_path / "ok.sql").write_text("ALTER ROLE review_iq_app BYPASSRLS;\n", encoding="utf-8")
    assert len(guard.check(tmp_path, allow)) == 1  # same file, different role


def test_main_uses_module_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "ALLOWLIST", {("x", "20990101000001_new.sql"): "test"})
    assert _run(tmp_path, "ALTER ROLE x BYPASSRLS;\n") == 0


def test_unreadable_file_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "bad.sql").write_bytes(b"\xff\xfe\x00 not utf8 \x80\x81")
    failures = guard.check(tmp_path, {})
    assert len(failures) == 1
    assert "failing closed" in failures[0]


def test_unterminated_constructs_fail_closed(tmp_path: Path) -> None:
    assert _run(tmp_path, "/* never closed\nALTER ROLE x NOBYPASSRLS;\n") == 1
    assert _run(tmp_path, "SELECT 'never closed;\n", name="b.sql") == 1


def test_empty_or_missing_dir_fails_closed(tmp_path: Path) -> None:
    assert guard.main([str(tmp_path)]) == 1
    assert guard.main([str(tmp_path / "does_not_exist")]) == 1


def test_real_repo_passes() -> None:
    assert guard.main([]) == 0


def test_hostile_new_migration_added_to_copy_of_real_migrations(tmp_path: Path) -> None:
    """The exact induced case: a NEW migration re-granting review_iq_app BYPASSRLS."""
    copy = tmp_path / "migrations"
    shutil.copytree(REAL_MIGRATIONS, copy)
    assert guard.main([str(copy)]) == 0  # baseline: the real set is clean
    (copy / "29990101000001_regrant.sql").write_text(
        "ALTER ROLE review_iq_app BYPASSRLS;\n", encoding="utf-8"
    )
    assert guard.main([str(copy)]) == 1
