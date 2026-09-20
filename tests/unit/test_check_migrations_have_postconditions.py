"""Tests for supabase/postconditions.py (grammar) and scripts/check_migrations_have_postconditions.py.

The guard is the PR-time half of the postcondition control; push.py is the deploy-time half
(tests/unit/test_push_postconditions.py). Hostile cases matter more than the happy path: the
failure mode this exists to stop is a check that reports success while verifying nothing, so
every malformed shape must FAIL, never degrade to "no postcondition declared, fine".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts import check_migrations_have_postconditions as guard

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "supabase"))
import postconditions as pcmod  # noqa: E402

REAL_MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

GOOD = "CREATE TABLE t (a int);\n-- @postcondition: t_exists\n-- SQL: SELECT to_regclass('public.t') IS NOT NULL\n"


def _run(tmp_path: Path, sql: str, name: str = "20990101000001_new.sql") -> int:
    (tmp_path / name).write_text(sql, encoding="utf-8")
    return guard.main([str(tmp_path)])


def _pc_sql(sql_line: str) -> str:
    return f"SELECT 1;\n-- @postcondition: some_check\n-- SQL: {sql_line}\n"


# ---------------------------------------------------------------- happy paths


def test_well_formed_postcondition_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, GOOD) == 0


def test_multi_line_sql_is_joined_and_trailing_semicolon_allowed(tmp_path: Path) -> None:
    sql = (
        "SELECT 1;\n-- @postcondition: two_lines\n-- SQL: SELECT true\n"
        "-- SQL:   AND false = false;\n"
    )
    assert _run(tmp_path, sql) == 0
    (pc,) = pcmod.parse_postconditions(sql)
    assert pc.sql == "SELECT true AND false = false"
    assert pc.name == "two_lines"
    assert pc.line == 2


def test_semicolon_and_comment_markers_inside_string_literal_are_fine(tmp_path: Path) -> None:
    sql = _pc_sql("SELECT 'a;b -- c /* d $ e' = 'a;b -- c /* d $ e'")
    assert _run(tmp_path, sql) == 0


def test_scoped_postcondition_with_reason_passes(tmp_path: Path) -> None:
    sql = (
        "SELECT 1;\n-- @postcondition: always_true\n-- SQL: SELECT true\n"
        "-- @postcondition: only_prod\n-- @scope: prod-only\n"
        "-- @scope-reason: CI has no such role\n-- SQL: SELECT true\n"
    )
    assert _run(tmp_path, sql) == 0
    plain, scoped = pcmod.parse_postconditions(sql)
    assert plain.scope is None
    assert scoped.scope == "prod-only"
    assert scoped.scope_reason == "CI has no such role"


def test_file_whose_every_postcondition_is_scoped_fails(tmp_path: Path) -> None:
    sql = (
        "SELECT 1;\n-- @postcondition: only_prod\n-- @scope: prod-only\n"
        "-- @scope-reason: CI has no such role\n-- SQL: SELECT true\n"
    )
    assert _run(tmp_path, sql) == 1


def test_do_block_begin_is_not_flagged_as_transaction_control(tmp_path: Path) -> None:
    sql = "DO $$ BEGIN\n  BEGIN\n    NULL;\n  END;\nEND $$;\n" + GOOD
    assert _run(tmp_path, sql) == 0


def test_allowlisted_file_with_reason_passes(tmp_path: Path) -> None:
    (tmp_path / "20990101000001_data_only.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assert guard.check(tmp_path, {"20990101000001_data_only.sql": "pure data migration"}) == []


# ---------------------------------------------------------------- missing / allowlist abuse


def test_no_postcondition_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(tmp_path, "CREATE TABLE t (a int);\n") == 1
    err = capsys.readouterr().err
    assert "20990101000001_new.sql" in err
    assert "no '-- @postcondition:' block" in err


def test_empty_dir_fails(tmp_path: Path) -> None:
    assert guard.main([str(tmp_path)]) == 1


def test_missing_dir_fails(tmp_path: Path) -> None:
    assert guard.main([str(tmp_path / "nope")]) == 1


def test_allowlist_with_empty_reason_fails(tmp_path: Path) -> None:
    (tmp_path / "20990101000001_x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    failures = guard.check(tmp_path, {"20990101000001_x.sql": "   "})
    assert any("empty reason" in f for f in failures)


def test_stale_allowlist_entry_naming_no_file_fails(tmp_path: Path) -> None:
    (tmp_path / "20990101000001_x.sql").write_text(GOOD, encoding="utf-8")
    failures = guard.check(tmp_path, {"20990101000009_gone.sql": "was data-only"})
    assert any("names no migration file" in f for f in failures)


def test_allowlisted_file_that_now_has_postconditions_fails(tmp_path: Path) -> None:
    (tmp_path / "20990101000001_x.sql").write_text(GOOD, encoding="utf-8")
    failures = guard.check(tmp_path, {"20990101000001_x.sql": "was data-only"})
    assert any("stale allowlist entry" in f for f in failures)


# ---------------------------------------------------------------- hostile SQL


@pytest.mark.parametrize(
    "sql_line, needle",
    [
        ("SELECT true; DROP TABLE public.organizations", "more than one statement"),
        ("SELECT true; SELECT true", "more than one statement"),
        ("SELECT true -- trailing comment", "comment marker"),
        ("SELECT true /* c */", "comment marker"),
        ("SELECT $$true$$::boolean", "dollar"),
        ("SELECT $f$true$f$::boolean", "dollar"),
        ("DELETE FROM public.organizations RETURNING true", "SELECT"),
        ("WITH x AS (SELECT true) SELECT * FROM x", "SELECT"),
        ("UPDATE t SET a = 1", "SELECT"),
        ("(SELECT true)", "SELECT"),
        ("SELECT true INTO public.leftover", "INTO"),
        ("SELECT true FROM t FOR UPDATE", "row-locking"),
        ("SELECT set_config('app.x', 'y', false) = 'y'", "set_config"),
        ("SELECT nextval('s') > 0", "nextval"),
        ("SELECT pg_terminate_backend(1)", "pg_terminate_backend"),
        ("SELECT 'unterminated", "unterminated"),
        ("", "no '-- SQL:' line"),
    ],
)
def test_hostile_sql_is_rejected(tmp_path: Path, sql_line: str, needle: str) -> None:
    assert _run(tmp_path, _pc_sql(sql_line)) == 1
    with pytest.raises(pcmod.PostconditionError) as exc:
        pcmod.parse_postconditions(_pc_sql(sql_line))
    assert needle in str(exc.value)


def test_semicolon_hidden_after_a_string_is_still_caught() -> None:
    with pytest.raises(pcmod.PostconditionError):
        pcmod.parse_postconditions(_pc_sql("SELECT 'a'; DROP TABLE t; SELECT 'b'"))


# ---------------------------------------------------------------- malformed block shapes


@pytest.mark.parametrize(
    "text",
    [
        # blank line between header and SQL -> header has no SQL, and the SQL is orphaned
        "-- @postcondition: a_b\n\n-- SQL: SELECT true\n",
        # SQL with no header at all
        "-- SQL: SELECT true\n",
        # misspelled directive must not silently vanish
        "-- @postcondtion: a_b\n-- SQL: SELECT true\n",
        # scope without reason
        "-- @postcondition: a_b\n-- @scope: prod-only\n-- SQL: SELECT true\n",
        # scope with empty reason
        "-- @postcondition: a_b\n-- @scope: ci-only\n-- @scope-reason:\n-- SQL: SELECT true\n",
        # unknown scope value
        "-- @postcondition: a_b\n-- @scope: staging\n-- @scope-reason: x\n-- SQL: SELECT true\n",
        # scope line detached from its header by another comment
        "-- @postcondition: a_b\n-- note\n-- @scope: ci-only\n-- @scope-reason: x\n-- SQL: SELECT true\n",
        # bad names
        "-- @postcondition: NotSnake\n-- SQL: SELECT true\n",
        "-- @postcondition: has-dash\n-- SQL: SELECT true\n",
        "-- @postcondition: 1abc\n-- SQL: SELECT true\n",
        "-- @postcondition:\n-- SQL: SELECT true\n",
        # duplicate names
        "-- @postcondition: dup\n-- SQL: SELECT true\n-- @postcondition: dup\n-- SQL: SELECT true\n",
        # header with no SQL at end of file
        "-- @postcondition: lonely\n",
        # header followed by an empty SQL line
        "-- @postcondition: empty_sql\n-- SQL:\n",
        # lower-case directive is not the grammar and must not pass as "no directive"
        "-- sql: SELECT true\n",
    ],
)
def test_malformed_blocks_fail(tmp_path: Path, text: str) -> None:
    assert _run(tmp_path, "SELECT 1;\n" + text) == 1


def test_indented_header_is_not_a_block_but_file_still_needs_a_real_one(tmp_path: Path) -> None:
    # Only column-0 comments count, so an indented header inside a DO body cannot satisfy
    # the requirement: the file has no real postcondition and must fail.
    assert (
        _run(tmp_path, "DO $$ BEGIN\n  -- @postcondition: x\n  -- SQL: SELECT true\nEND $$;\n") == 1
    )


# ---------------------------------------------------------------- transaction control / IO


@pytest.mark.parametrize(
    "stmt", ["BEGIN;", "COMMIT;", "ROLLBACK;", "  begin;", "START TRANSACTION;"]
)
def test_own_transaction_control_fails(tmp_path: Path, stmt: str) -> None:
    assert _run(tmp_path, f"{stmt}\nSELECT 1;\n" + GOOD) == 1


def test_transaction_control_in_a_comment_is_ignored(tmp_path: Path) -> None:
    assert _run(tmp_path, "-- BEGIN; COMMIT;\nSELECT 1;\n" + GOOD) == 0


def test_undecodable_file_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "20990101000001_bad.sql").write_bytes(b"\xff\xfe\x00SELECT")
    assert guard.main([str(tmp_path)]) == 1


def test_too_many_args_is_usage_error() -> None:
    assert guard.main(["a", "b"]) == 2


# ---------------------------------------------------------------- scope semantics


def test_applies_to_scope_matrix() -> None:
    plain = pcmod.Postcondition("a", "SELECT true", 1)
    prod = pcmod.Postcondition("b", "SELECT true", 1, "prod-only", "r")
    ci = pcmod.Postcondition("c", "SELECT true", 1, "ci-only", "r")
    assert [pcmod.applies_to(p, "prod") for p in (plain, prod, ci)] == [True, True, False]
    assert [pcmod.applies_to(p, "ci") for p in (plain, prod, ci)] == [True, False, True]
    with pytest.raises(ValueError):
        pcmod.applies_to(plain, "staging")
