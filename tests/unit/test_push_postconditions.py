"""Unit tests for supabase/push.py's postcondition enforcement, against a fake DB-API connection.

The failure these exist to stop: push.py used to ledger a file whenever its SQL ran WITHOUT
ERROR, so a non-owner REVOKE (a silent no-op) was recorded as applied while production kept
the grant. The fake connection models just enough transaction semantics (ledger rows staged
until commit, dropped on rollback) to prove the ordering guarantee: file SQL -> postconditions
-> ledger INSERT -> COMMIT, all one transaction, and a false postcondition means ROLLBACK and
no ledger row. Real-Postgres behavior is covered in tests/integration/test_push_postconditions.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "supabase"))


def _load_push() -> ModuleType:
    spec = importlib.util.spec_from_file_location("push_under_test", ROOT / "supabase" / "push.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves the defining module via sys.modules, so register before exec.
    sys.modules["push_under_test"] = module
    spec.loader.exec_module(module)
    return module


push = _load_push()

PC_TRUE = "SELECT true"
PC_FALSE = "SELECT false"


def _migration(body: str, *pcs: tuple[str, str]) -> str:
    """A migration file: `body` plus a postcondition block per (name, sql) pair."""
    blocks = "".join(f"-- @postcondition: {n}\n-- SQL: {s}\n" for n, s in pcs)
    return f"{body}\n{blocks}"


def _scoped(name: str, scope: str, sql: str) -> str:
    return (
        f"-- @postcondition: {name}\n-- @scope: {scope}\n-- @scope-reason: differs\n-- SQL: {sql}\n"
    )


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        text = " ".join(sql.split())
        self.conn.log.append(text)
        self._rows = []
        if text.startswith("INSERT INTO public._migrations"):
            assert params is not None
            self.conn.staged.add(params[0])
            return
        if text == "SELECT filename FROM public._migrations":
            self._rows = [(f,) for f in sorted(self.conn.ledger)]
            return
        if text == "SELECT to_regclass('public._migrations') IS NOT NULL":
            self._rows = [(self.conn.ledger_exists,)]
            return
        if text in self.conn.results:
            outcome = self.conn.results[text]
            if isinstance(outcome, Exception):
                raise outcome
            self._rows = outcome
            return
        if text == PC_TRUE:
            self._rows = [(True,)]
        elif text == PC_FALSE:
            self._rows = [(False,)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self) -> None:
        self.ledger: set[str] = set()
        self.ledger_exists = True
        self.staged: set[str] = set()
        self.log: list[str] = []
        self.results: dict[str, Any] = {}
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.autocommit = False
        self.readonly = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.ledger |= self.staged
        self.staged.clear()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.staged.clear()

    def set_session(self, readonly: bool = False) -> None:
        self.readonly = readonly

    def close(self) -> None:
        self.closed = True


class Env:
    """Helper namespace handed to each test."""

    def __init__(self, mig: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mig = mig
        self.conn = FakeConn()
        self._monkeypatch = monkeypatch

    def write(self, name: str, text: str) -> None:
        (self.mig / name).write_text(text, encoding="utf-8")

    def run(self, *argv: str) -> None:
        self._monkeypatch.setattr(push.psycopg2, "connect", lambda *_a, **_k: self.conn)
        push.main(list(argv))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    """Point push.py at a tmp migrations dir and a FakeConn."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    monkeypatch.setattr(push, "MIGRATIONS_DIR", mig)
    monkeypatch.setenv("SUPABASE_DIRECT_URL", "postgresql://fake/fake")
    monkeypatch.setattr(push, "ALLOWLIST", {})
    return Env(mig, monkeypatch)


def _executed(conn: FakeConn, marker: str) -> bool:
    return any(marker in entry for entry in conn.log)


# ---------------------------------------------------------------- apply: happy path + ordering


def test_apply_runs_file_then_postcondition_then_ledger_then_commit(env: Env) -> None:
    env.write("001_a.sql", _migration("CREATE TABLE a_marker (x int);", ("a_ok", PC_TRUE)))
    env.run()
    log = env.conn.log
    i_file = next(i for i, e in enumerate(log) if "a_marker" in e)
    i_pc = next(i for i, e in enumerate(log) if e == PC_TRUE)
    i_ins = next(i for i, e in enumerate(log) if e.startswith("INSERT INTO public._migrations"))
    assert i_file < i_pc < i_ins
    # the postcondition ran inside a savepoint that was always rolled back and released
    assert log[i_pc - 1] == "SAVEPOINT pc_eval"
    assert log[i_pc + 1 : i_pc + 3] == [
        "ROLLBACK TO SAVEPOINT pc_eval",
        "RELEASE SAVEPOINT pc_eval",
    ]
    assert env.conn.ledger == {"001_a.sql"}
    assert env.conn.closed


# ---------------------------------------------------------------- apply: failures roll back


@pytest.mark.parametrize(
    ("outcome", "needle"),
    [
        ([(False,)], "returned False"),
        ([(None,)], "returned None"),
        ([], "expected exactly 1 row"),
        ([(True,), (True,)], "expected exactly 1 row"),
        ([(True, True)], "expected exactly 1 row"),
        ([(1,)], "returned 1"),
        ([("t",)], "returned 't'"),
        (RuntimeError("boom"), "errored: RuntimeError: boom"),
    ],
)
def test_postcondition_not_exactly_true_rolls_back_and_does_not_ledger(
    env: Env, capsys: pytest.CaptureFixture[str], outcome: Any, needle: str
) -> None:
    env.write("001_a.sql", _migration("CREATE TABLE a_marker (x int);", ("must_hold", "SELECT x")))
    env.conn.results["SELECT x"] = outcome
    with pytest.raises(SystemExit) as exc:
        env.run()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "POSTCONDITION FAILED: 001_a.sql :: must_hold" in err
    assert needle in err
    assert "NOT recorded in public._migrations" in err
    assert env.conn.ledger == set()
    assert env.conn.rollbacks >= 1
    # the only commit is the ledger-table bootstrap that precedes any migration
    assert env.conn.commits == 1


def test_failure_stops_the_run_earlier_files_stay_committed_later_files_untouched(
    env: Env,
) -> None:
    env.write("001_a.sql", _migration("SELECT 'first_marker';", ("ok_one", PC_TRUE)))
    env.write("002_b.sql", _migration("SELECT 'second_marker';", ("bad_one", PC_FALSE)))
    env.write("003_c.sql", _migration("SELECT 'third_marker';", ("ok_three", PC_TRUE)))
    with pytest.raises(SystemExit):
        env.run()
    assert env.conn.ledger == {"001_a.sql"}
    assert _executed(env.conn, "second_marker")
    assert not _executed(env.conn, "third_marker")


def test_one_failing_postcondition_among_several_fails_the_file(env: Env) -> None:
    env.write(
        "001_a.sql",
        _migration(
            "SELECT 1;", ("good_one", PC_TRUE), ("bad_one", PC_FALSE), ("good_two", PC_TRUE)
        ),
    )
    with pytest.raises(SystemExit):
        env.run()
    assert env.conn.ledger == set()


def test_the_files_own_sql_error_still_rolls_back_and_does_not_ledger(env: Env) -> None:
    text = _migration("SELECT explode();", ("ok", PC_TRUE))
    env.write("001_a.sql", text)
    env.conn.results[" ".join(text.split())] = RuntimeError("relation does not exist")
    with pytest.raises(RuntimeError):
        env.run()
    assert env.conn.ledger == set()
    assert env.conn.rollbacks >= 1


# ---------------------------------------------------------------- apply: fail closed on no postcondition


def test_file_without_postcondition_is_refused_before_anything_is_applied(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write("001_a.sql", _migration("SELECT 'a_marker';", ("a_ok", PC_TRUE)))
    env.write("002_b.sql", "SELECT 'b_marker';\n")
    with pytest.raises(SystemExit) as exc:
        env.run()
    assert exc.value.code == 1
    assert "REFUSED 002_b.sql: missing" in capsys.readouterr().err
    # fail closed BEFORE applying: 001 (which is fine) must not have run either
    assert not _executed(env.conn, "a_marker")
    assert not _executed(env.conn, "b_marker")
    assert env.conn.ledger == set()


def test_allowlisted_file_applies_without_postcondition(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(push, "ALLOWLIST", {"001_a.sql": "pure data migration"})
    env.write("001_a.sql", "SELECT 'a_marker';\n")
    env.run()
    assert env.conn.ledger == {"001_a.sql"}


def test_malformed_postcondition_is_refused(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.write(
        "001_a.sql", "SELECT 'a_marker';\n-- @postcondition: x\n-- SQL: SELECT 1; DROP TABLE t\n"
    )
    with pytest.raises(SystemExit):
        env.run()
    assert "REFUSED 001_a.sql: malformed" in capsys.readouterr().err
    assert not _executed(env.conn, "a_marker")


def test_file_with_its_own_commit_is_refused(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    env.write("001_a.sql", _migration("BEGIN;\nSELECT 'a_marker';\nCOMMIT;", ("a_ok", PC_TRUE)))
    with pytest.raises(SystemExit):
        env.run()
    assert "own transaction control" in capsys.readouterr().err
    assert not _executed(env.conn, "a_marker")


# ---------------------------------------------------------------- scope handling


def test_prod_only_condition_is_skipped_for_ci_target(env: Env) -> None:
    env.write(
        "001_a.sql",
        "SELECT 'a_marker';\n"
        + _scoped("only_prod", "prod-only", PC_FALSE)
        + "-- @postcondition: always\n-- SQL: SELECT true\n",
    )
    env.run("--target", "ci")
    assert env.conn.ledger == {"001_a.sql"}
    assert PC_FALSE not in env.conn.log  # skipped, not merely ignored


def test_prod_only_condition_runs_for_prod_target_and_can_fail(env: Env) -> None:
    env.write(
        "001_a.sql",
        "SELECT 'a_marker';\n"
        + _scoped("only_prod", "prod-only", PC_FALSE)
        + "-- @postcondition: always\n-- SQL: SELECT true\n",
    )
    with pytest.raises(SystemExit):
        env.run("--target", "prod")
    assert env.conn.ledger == set()


def test_ci_only_condition_skipped_for_prod_and_default_target_is_prod(env: Env) -> None:
    env.write(
        "001_a.sql",
        "SELECT 'a_marker';\n"
        + _scoped("only_ci", "ci-only", PC_FALSE)
        + "-- @postcondition: always\n-- SQL: SELECT true\n",
    )
    env.run()  # default target == prod
    assert env.conn.ledger == {"001_a.sql"}


def test_file_whose_only_postconditions_are_scoped_is_refused(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write("001_a.sql", "SELECT 'a_marker';\n" + _scoped("only_ci", "ci-only", PC_TRUE))
    with pytest.raises(SystemExit):
        env.run("--target", "prod")
    assert "at least one unscoped postcondition" in capsys.readouterr().err
    assert not _executed(env.conn, "a_marker")


# ---------------------------------------------------------------- --through


def test_through_applies_only_files_up_to_and_including_it(env: Env) -> None:
    for n in ("001_a.sql", "002_b.sql", "003_c.sql"):
        env.write(n, _migration(f"SELECT '{n}_marker';", ("ok", PC_TRUE)))
    env.run("--through", "002_b.sql")
    assert env.conn.ledger == {"001_a.sql", "002_b.sql"}


def test_through_unknown_file_is_a_usage_error(env: Env) -> None:
    env.write("001_a.sql", _migration("SELECT 1;", ("ok", PC_TRUE)))
    with pytest.raises(SystemExit) as exc:
        env.run("--through", "nope.sql")
    assert exc.value.code == 2
    assert env.conn.log == []


# ---------------------------------------------------------------- --mark-applied-all


def test_mark_applied_all_records_files_whose_postconditions_hold(env: Env) -> None:
    env.write("001_a.sql", _migration("SELECT 'a_marker';", ("ok", PC_TRUE)))
    env.run("--mark-applied-all")
    assert env.conn.ledger == {"001_a.sql"}
    # the file's own SQL is NOT run by --mark-applied-all
    assert not _executed(env.conn, "a_marker")


def test_mark_applied_all_refuses_a_failed_postcondition_and_force_does_not_override(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write("001_a.sql", _migration("SELECT 1;", ("bad", PC_FALSE)))
    for argv in (["--mark-applied-all"], ["--mark-applied-all", "--force"]):
        env.conn = FakeConn()  # fresh database per attempt
        with pytest.raises(SystemExit) as exc:
            env.run(*argv)
        assert exc.value.code == 1
        assert env.conn.ledger == set()
    out = capsys.readouterr().out
    assert "REFUSED (postcondition failed, not marked): 001_a.sql" in out
    assert "bad: returned False" in out


def test_mark_applied_all_refuses_a_file_with_no_postcondition_even_with_force(env: Env) -> None:
    env.write("001_a.sql", "SELECT 1;\n")
    with pytest.raises(SystemExit):
        env.run("--mark-applied-all", "--force")
    assert env.conn.ledger == set()


def test_mark_applied_all_only_records_the_files_that_pass(env: Env) -> None:
    env.write("001_a.sql", _migration("SELECT 1;", ("bad", PC_FALSE)))
    env.write("002_b.sql", _migration("SELECT 2;", ("ok", PC_TRUE)))
    with pytest.raises(SystemExit):
        env.run("--mark-applied-all")
    assert env.conn.ledger == {"002_b.sql"}


# ---------------------------------------------------------------- --verify


def test_verify_prints_table_with_every_status_and_exits_1_on_fail(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    env.conn.ledger = {
        "001_pass.sql",
        "002_fail.sql",
        "003_none.sql",
        "004_scoped.sql",
        "999_gone.sql",
    }
    env.write("001_pass.sql", _migration("SELECT 1;", ("holds", PC_TRUE)))
    env.write("002_fail.sql", _migration("SELECT 1;", ("broken", PC_FALSE)))
    env.write("003_none.sql", "SELECT 1;\n")
    env.write(
        "004_scoped.sql",
        _migration("SELECT 1;", ("plain", PC_TRUE)) + _scoped("prod_thing", "prod-only", PC_TRUE),
    )
    env.write("005_unledgered.sql", _migration("SELECT 1;", ("later", PC_TRUE)))
    with pytest.raises(SystemExit) as exc:
        env.run("--verify", "--target", "ci")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    statuses = {}
    for line in out.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) == 3:
            statuses[(cells[0], cells[1])] = cells[2].split("  --")[0]
    assert statuses[("001_pass.sql", "holds")] == "PASS"
    assert statuses[("002_fail.sql", "broken")] == "FAIL"
    assert statuses[("003_none.sql", "-")] == "NO_POSTCONDITION"
    assert statuses[("004_scoped.sql", "plain")] == "PASS"
    assert statuses[("004_scoped.sql", "prod_thing")] == "SKIPPED(scope)"
    assert statuses[("999_gone.sql", "-")] == "LEDGER_ROW_NO_FILE"
    assert statuses[("005_unledgered.sql", "-")] == "NOT_IN_LEDGER"
    assert "returned False" in out


def test_verify_exit_zero_when_all_pass_even_with_unledgered_files_and_orphan_rows(
    env: Env,
) -> None:
    env.conn.ledger = {"001_a.sql", "999_gone.sql"}
    env.write("001_a.sql", _migration("SELECT 1;", ("holds", PC_TRUE)))
    env.write("002_new.sql", _migration("SELECT 1;", ("holds", PC_TRUE)))
    env.run("--verify")  # returns normally, i.e. exit code 0


def test_verify_is_read_only_never_inserts_commits_or_runs_migration_sql(env: Env) -> None:
    env.conn.ledger = {"001_a.sql"}
    env.write("001_a.sql", _migration("CREATE TABLE a_marker (x int);", ("holds", PC_TRUE)))
    env.run("--verify")
    assert env.conn.readonly is True
    assert env.conn.commits == 0
    assert not any(e.startswith(("INSERT", "CREATE")) for e in env.conn.log)
    assert not _executed(env.conn, "a_marker")


def test_verify_on_a_database_with_no_ledger_does_not_create_it(env: Env) -> None:
    env.conn.ledger_exists = False
    env.write("001_a.sql", _migration("SELECT 1;", ("holds", PC_TRUE)))
    env.run("--verify")
    assert not any("CREATE TABLE" in e for e in env.conn.log)


def test_verify_treats_a_malformed_block_in_a_ledgered_file_as_fail(env: Env) -> None:
    env.conn.ledger = {"001_a.sql"}
    env.write("001_a.sql", "SELECT 1;\n-- @postcondition: x\n-- SQL: SELECT 1; DROP TABLE t\n")
    with pytest.raises(SystemExit) as exc:
        env.run("--verify")
    assert exc.value.code == 1


def test_verify_reports_allowlisted_file_without_failing(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(push, "ALLOWLIST", {"001_a.sql": "pure data migration"})
    env.conn.ledger = {"001_a.sql"}
    env.write("001_a.sql", "SELECT 1;\n")
    env.run("--verify")
    assert "ALLOWLISTED" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra", [["--dry-run"], ["--mark-applied-all"], ["--through", "001_a.sql"]]
)
def test_verify_cannot_be_combined_with_write_actions(env: Env, extra: list[str]) -> None:
    env.write("001_a.sql", _migration("SELECT 1;", ("holds", PC_TRUE)))
    with pytest.raises(SystemExit) as exc:
        env.run("--verify", *extra)
    assert exc.value.code == 2
