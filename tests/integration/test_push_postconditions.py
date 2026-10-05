"""Migration postconditions against a REAL Postgres (Session 15d).

Builds a throwaway database the way CI now does (supabase/ci/apply_migrations_ci.py: roles
bootstrapped, migrations through 20260801000001 as the superuser, ownership handed to
review_iq_migrator, every later migration applied through push.py AS the non-superuser
migrator) and proves four things the unit tests (tests/unit/test_push_postconditions.py, fake
connection) cannot:

  1. every postcondition of every migration is TRUE on a fresh migrator-built schema;
  2. no postcondition is vacuous -- for every migration (UNDO_CASES) the
     effect is undone with real DDL and the condition then evaluates to FALSE (not an error);
  3. the real incident reproduces: a `REVOKE ... ON FUNCTION` by a role that does not own the
     function completes WITHOUT ERROR and changes NOTHING, and push.py's postcondition rejects
     the file and does not ledger it (while the same file run as the owner passes);
  4. the migrator-apply layer is real: review_iq_migrator is not a superuser, every file is
     ledgered, and nothing in public is owned by anyone else.

Safety: this module creates and drops a database, so it REFUSES to run unless TEST_DB_HOST is
set explicitly to a local host (the shared helper's default is the production host).

Marked 'integration' -- skipped in default CI; run in the pre-cutover-verification job, or
locally against a throwaway container:
    TEST_DB_HOST=localhost TEST_DB_PORT=5433 TEST_DB_SSLMODE=disable \\
    SUPABASE_DB_PASSWORD=ci_postgres_password \\
    uv run pytest tests/integration/test_push_postconditions.py -v -m integration --no-cov
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
sys.path.insert(0, str(ROOT / "supabase"))

_LOCAL_HOSTS = {"localhost", "127.0.0.1"}
_HOST = os.environ.get("TEST_DB_HOST", "")
if _HOST not in _LOCAL_HOSTS:
    pytest.skip(
        "refusing to create/drop databases: set TEST_DB_HOST to a LOCAL throwaway Postgres "
        f"(got {_HOST!r})",
        allow_module_level=True,
    )
_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
_SUPER_PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD", "ci_postgres_password")


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # @dataclass resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


push = _load("push_integration", ROOT / "supabase" / "push.py")
ci_build = _load(
    "apply_migrations_ci_integration", ROOT / "supabase" / "ci" / "apply_migrations_ci.py"
)


def _dsn(user: str, password: str, dbname: str) -> str:
    return ci_build.dsn(user, password, _HOST, _PORT, dbname)


class BuiltDb:
    """A freshly built database plus connection helpers."""

    def __init__(self, dbname: str) -> None:
        self.dbname = dbname
        self.super_dsn = _dsn("postgres", _SUPER_PASSWORD, dbname)
        self.migrator_dsn = _dsn("review_iq_migrator", ci_build.MIGRATOR_PASSWORD, dbname)

    def superuser(self, autocommit: bool = True) -> Any:
        conn = psycopg2.connect(self.super_dsn, connect_timeout=15)
        conn.autocommit = autocommit
        return conn


@pytest.fixture(scope="module")
def built_db() -> Iterator[BuiltDb]:
    dbname = f"pc_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(_dsn("postgres", _SUPER_PASSWORD, "postgres"), connect_timeout=15)
    admin.autocommit = True  # CREATE/DROP DATABASE cannot run inside a transaction
    admin.cursor().execute(f'CREATE DATABASE "{dbname}"')
    try:
        ci_build.build(_HOST, _PORT, dbname, _SUPER_PASSWORD)
        yield BuiltDb(dbname)
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        admin.close()


def _all_postconditions() -> list[tuple[str, Any]]:
    out = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        for pc in push.parse_postconditions(path.read_text(encoding="utf-8")):
            out.append((path.name, pc))
    return out


def _find(filename: str, name: str) -> Any:
    for fname, pc in _all_postconditions():
        if fname == filename and pc.name == name:
            return pc
    raise AssertionError(f"no postcondition {name!r} in {filename}")


# ---------------------------------------------------------------- 1 + 4: the build itself


def test_verify_passes_every_postcondition_on_the_migrator_built_schema(built_db: BuiltDb) -> None:
    env = {
        **os.environ,
        "SUPABASE_DIRECT_URL": built_db.migrator_dsn,
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "supabase" / "push.py"), "--verify", "--target", "ci"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    print(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    expected = sum(1 for _, pc in _all_postconditions() if pc.scope in (None, "ci-only"))
    assert proc.stdout.count(" | PASS") == expected
    assert "FAIL" not in proc.stdout
    assert "NO_POSTCONDITION" not in proc.stdout
    assert "NOT_IN_LEDGER" not in proc.stdout


def test_migrator_applied_the_later_migrations_and_owns_everything_in_public(
    built_db: BuiltDb,
) -> None:
    conn = built_db.superuser()
    try:
        cur = conn.cursor()
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = 'review_iq_migrator'")
        assert cur.fetchone() == (False,), "the migrator must NOT be a superuser"
        cur.execute("SELECT filename FROM public._migrations")
        ledgered = {r[0] for r in cur.fetchall()}
        assert ledgered == {p.name for p in MIGRATIONS.glob("*.sql")}
        cur.execute(
            "SELECT count(*) FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace "
            "AND c.relkind = 'r' AND pg_get_userbyid(c.relowner) <> 'review_iq_migrator'"
        )
        assert cur.fetchone() == (0,)
    finally:
        conn.close()


def test_schema_snapshot_reports_the_migrator_as_owner_of_every_public_object(
    built_db: BuiltDb,
) -> None:
    """Exercises the owner queries added to extract_schema_snapshot.py (the drift check's
    ephemeral side): CI and production must agree, and after the handoff that agreement is
    'everything is owned by review_iq_migrator'."""
    extractor = _load(
        "extract_schema_snapshot_integration", ROOT / "scripts" / "extract_schema_snapshot.py"
    )
    snap = extractor.extract_snapshot(built_db.super_dsn)
    rel, fn = snap["relation_owners"], snap["function_owners"]
    assert {r["table_name"] for r in rel} >= {"organizations", "leads", "_migrations"}
    assert {f["function_name"] for f in fn} >= {"current_org_id", "resolve_org_for_user"}
    assert {r["owner"] for r in rel} == {"review_iq_migrator"}
    assert {f["owner"] for f in fn} == {"review_iq_migrator"}


# ---------------------------------------------------------------- 2: non-vacuous


# (migration file, postcondition name, SQL that undoes the migration's effect)
UNDO_CASES: list[tuple[str, str, str]] = [
    (
        "20260510000001_create_tables.sql",
        "organizations_columns",
        "ALTER TABLE public.organizations ALTER COLUMN name DROP NOT NULL",
    ),
    (
        "20260510000001_create_tables.sql",
        "tenant_foreign_keys",
        "ALTER TABLE public.api_keys DROP CONSTRAINT api_keys_org_id_fkey",
    ),
    (
        "20260510000002_rls_policies.sql",
        "rls_enabled_on_original_tenant_tables",
        "ALTER TABLE public.api_keys DISABLE ROW LEVEL SECURITY",
    ),
    (
        "20260510000002_rls_policies.sql",
        "original_tenant_policies_scope_by_org_or_deny",
        "DROP POLICY api_keys_anon_deny ON public.api_keys",
    ),
    (
        "20260511000002_monthly_quota.sql",
        "usage_column_renamed_to_monthly_usage",
        "ALTER TABLE public.api_keys RENAME COLUMN monthly_usage TO usage",
    ),
    (
        "20260511000005_usage_records_token_columns.sql",
        "usage_records_token_columns",
        "ALTER TABLE public.usage_records ALTER COLUMN tokens_in DROP NOT NULL",
    ),
    (
        "20260619000001_review_id.sql",
        "review_id_generated_columns",
        "ALTER TABLE public.extractions ALTER COLUMN review_id DROP EXPRESSION",
    ),
    (
        "20260619000002_authenticity_audits_dedup.sql",
        "authenticity_audits_org_review_hash_unique",
        "ALTER TABLE public.authenticity_audits "
        "DROP CONSTRAINT authenticity_audits_org_review_hash_unique",
    ),
    (
        "20260622000001_shopify_installations.sql",
        "shopify_installations_indexes",
        "DROP INDEX public.idx_shopify_inst_org_id",
    ),
    (
        "20260711000001_alert_event_types.sql",
        "alert_preferences_event_type_allows_detector_types",
        "ALTER TABLE public.alert_preferences DROP CONSTRAINT alert_preferences_event_type_check; "
        "ALTER TABLE public.alert_preferences ADD CONSTRAINT alert_preferences_event_type_check "
        "CHECK (event_type IN ('high_urgency'))",
    ),
    (
        "20260711000003_quota_requests_cascade.sql",
        "quota_requests_org_fk_cascades",
        "ALTER TABLE public.quota_requests DROP CONSTRAINT quota_requests_org_id_fkey; "
        "ALTER TABLE public.quota_requests ADD CONSTRAINT quota_requests_org_id_fkey "
        "FOREIGN KEY (org_id) REFERENCES public.organizations (id)",
    ),
    (
        "20260726000001_review_iq_app_role.sql",
        "review_iq_app_role_login_and_authenticated_membership",
        "REVOKE authenticated FROM review_iq_app",
    ),
    (
        "20260801000001_role_separation_bypassrls_remediation.sql",
        "migrator_and_admin_roles",
        "ALTER ROLE review_iq_migrator NOBYPASSRLS",
    ),
    (
        "20260801000001_role_separation_bypassrls_remediation.sql",
        "webhook_org_resolvers_are_narrow_security_definers",
        "GRANT EXECUTE ON FUNCTION public.resolve_org_for_shopify_shop(text) TO anon",
    ),
    (
        "20260801000002_tenant_resolvers_auth_signup.sql",
        "tenant_resolver_and_writer_functions_hardened",
        "ALTER FUNCTION public.resolve_org_for_user(uuid) SECURITY INVOKER",
    ),
    (
        "20260817000002_wave1_grant_narrowing.sql",
        "wave1_tables_grants_narrowed",
        "GRANT INSERT ON public.shopify_installations TO authenticated",
    ),
    (
        "20260817000003_cross_org_sweep_resolvers.sql",
        "cross_org_sweep_functions_hardened",
        "REVOKE EXECUTE ON FUNCTION public.list_orgs_with_daily_digest() FROM review_iq_app",
    ),
    (
        "20260817000004_wave2_grant_narrowing.sql",
        "wave2_tables_grants_narrowed",
        "GRANT UPDATE ON public.organizations TO authenticated",
    ),
    (
        "20260817000004_wave2_grant_narrowing.sql",
        "review_iq_admin_explicit_grants_on_organizations_and_api_keys",
        "REVOKE INSERT ON public.organizations FROM review_iq_admin",
    ),
    (
        "20260817000005_wave3_grant_narrowing.sql",
        "wave3_tables_grants_narrowed",
        "GRANT DELETE ON public.corrections TO authenticated",
    ),
    (
        "20260905000001_demo_daily_usage.sql",
        "demo_daily_usage_grants_locked_to_review_iq_app",
        "GRANT DELETE ON public.demo_daily_usage TO review_iq_app",
    ),
    (
        "20260905000002_extraction_costs_allow_demo_rows.sql",
        "extraction_costs_org_id_nullable_and_source_column",
        "ALTER TABLE public.extraction_costs ALTER COLUMN org_id SET NOT NULL",
    ),
    (
        "20260911000001_extraction_costs_precision.sql",
        "extraction_costs_cost_columns_have_declared_precision",
        "ALTER TABLE public.extraction_costs ALTER COLUMN cost_usd TYPE numeric",
    ),
    (
        "20260912000001_organizations_retention_mode.sql",
        "organizations_retention_consistent_constraint",
        "ALTER TABLE public.organizations DROP CONSTRAINT organizations_retention_consistent",
    ),
    (
        "20260912000002_extractions_grant_delete.sql",
        "extractions_authenticated_can_select_insert_delete_only",
        "REVOKE DELETE ON public.extractions FROM authenticated",
    ),
    (
        "20260912000003_close_pre_existing_grant_drift.sql",
        "current_org_id_not_executable_by_anon_or_public",
        "GRANT EXECUTE ON FUNCTION public.current_org_id() TO anon",
    ),
    (
        "20260912000003_close_pre_existing_grant_drift.sql",
        "current_org_id_not_executable_by_anon_or_public",
        "GRANT EXECUTE ON FUNCTION public.current_org_id() TO PUBLIC",
    ),
    (
        "20260912000003_close_pre_existing_grant_drift.sql",
        "demo_daily_usage_service_role_has_all",
        "REVOKE TRUNCATE ON public.demo_daily_usage FROM service_role",
    ),
    (
        "20260917000001_migrations_table_anon_grant.sql",
        "migrations_ledger_closed_to_anon_and_authenticated",
        "GRANT SELECT ON public._migrations TO anon",
    ),
    (
        "20260920000001_leads.sql",
        "leads_rls_and_review_iq_app_only_policies",
        "DROP POLICY leads_review_iq_app_insert ON public.leads",
    ),
    (
        "20260920000001_leads.sql",
        "leads_review_iq_app_privileges_are_column_narrow",
        "GRANT SELECT ON public.leads TO review_iq_app",
    ),
    (
        "20260920000002_leads_no_service_role.sql",
        "leads_service_role_has_no_table_privileges",
        "GRANT SELECT ON public.leads TO service_role",
    ),
    (
        "20260920000002_leads_no_service_role.sql",
        "leads_service_role_has_no_column_privileges",
        "GRANT SELECT (id) ON public.leads TO service_role",
    ),
    (
        "20260920000003_public_objects_owned_by_migrator.sql",
        "public_tables_sequences_views_owned_by_migrator",
        "ALTER TABLE public.leads OWNER TO postgres",
    ),
    (
        "20260920000003_public_objects_owned_by_migrator.sql",
        "public_functions_owned_by_migrator",
        "ALTER FUNCTION public.list_orgs_with_daily_digest() OWNER TO postgres",
    ),
    (
        "20260920000003_public_objects_owned_by_migrator.sql",
        "current_org_id_owned_by_migrator",
        "ALTER FUNCTION public.current_org_id() OWNER TO postgres",
    ),
    # Session 16 (V4b): the 17 migrations that had no undo case (26 of 43 covered before).
    (
        "20260511000001_add_key_prefix.sql",
        "api_keys_key_prefix_column",
        "ALTER TABLE public.api_keys ALTER COLUMN key_prefix DROP NOT NULL",
    ),
    (
        "20260511000001_add_key_prefix.sql",
        "api_keys_key_prefix_index",
        "DROP INDEX public.idx_api_keys_key_prefix",
    ),
    (
        "20260511000003_revoked_at.sql",
        "api_keys_revoked_at_column",
        "ALTER TABLE public.api_keys DROP COLUMN revoked_at",
    ),
    (
        "20260511000004_extractions_flat_columns.sql",
        "extractions_flat_columns",
        "ALTER TABLE public.extractions DROP COLUMN review_text",
    ),
    (
        "20260511000004_extractions_flat_columns.sql",
        "extraction_jsonb_column_is_nullable",
        "ALTER TABLE public.extractions ALTER COLUMN extraction SET NOT NULL",
    ),
    (
        "20260511000004_extractions_flat_columns.sql",
        "extractions_org_input_hash_unique_and_indexes",
        "DROP INDEX public.idx_extractions_urgency_org",
    ),
    (
        "20260511000006_batch_jobs.sql",
        "batch_jobs_columns_and_key",
        "ALTER TABLE public.batch_jobs DROP COLUMN source_columns",
    ),
    (
        "20260511000006_batch_jobs.sql",
        "batch_jobs_org_fk_and_index",
        "DROP INDEX public.idx_batch_jobs_org_id",
    ),
    (
        "20260611000001_authenticity_audits.sql",
        "authenticity_audits_columns_and_index",
        "DROP INDEX public.idx_authenticity_audits_org_created",
    ),
    (
        "20260611000001_authenticity_audits.sql",
        "authenticity_audits_rls_and_policies",
        "ALTER TABLE public.authenticity_audits DISABLE ROW LEVEL SECURITY",
    ),
    (
        "20260613000001_batch_jobs_rls.sql",
        "batch_jobs_rls_and_policies",
        "DROP POLICY batch_jobs_anon_deny ON public.batch_jobs",
    ),
    (
        "20260619000003_corrections.sql",
        "corrections_columns_and_constraints",
        "ALTER TABLE public.corrections DROP COLUMN correction_note",
    ),
    (
        "20260619000003_corrections.sql",
        "corrections_indexes",
        "DROP INDEX public.idx_corrections_org_review_id",
    ),
    (
        "20260619000003_corrections.sql",
        "corrections_rls_and_policies",
        "ALTER TABLE public.corrections DISABLE ROW LEVEL SECURITY",
    ),
    (
        "20260621000001_alerts.sql",
        "alert_preferences_columns_and_constraints",
        "DROP INDEX public.idx_alert_prefs_org_id",
    ),
    (
        "20260621000001_alerts.sql",
        "alert_log_columns_and_indexes",
        "DROP INDEX public.idx_alert_log_org_review",
    ),
    (
        "20260621000001_alerts.sql",
        "alert_tables_rls_and_policies",
        "DROP POLICY alert_log_anon_deny ON public.alert_log",
    ),
    (
        "20260621000002_org_notification_email.sql",
        "organizations_notification_email_column",
        "ALTER TABLE public.organizations DROP COLUMN notification_email",
    ),
    (
        "20260702000001_google_business_installations.sql",
        "google_business_installations_columns_and_constraints",
        "ALTER TABLE public.google_business_installations DROP COLUMN revoked_at",
    ),
    (
        "20260702000001_google_business_installations.sql",
        "google_business_installations_indexes",
        "DROP INDEX public.idx_gbp_inst_org_id",
    ),
    (
        "20260702000001_google_business_installations.sql",
        "google_business_installations_rls_and_policies",
        "DROP POLICY gbp_inst_anon_deny ON public.google_business_installations",
    ),
    (
        "20260709000001_batch_job_rows.sql",
        "batch_job_rows_columns_and_keys",
        "ALTER TABLE public.batch_job_rows DROP COLUMN error",
    ),
    (
        "20260709000001_batch_job_rows.sql",
        "batch_job_rows_indexes",
        "DROP INDEX public.idx_batch_job_rows_org_id",
    ),
    (
        "20260709000001_batch_job_rows.sql",
        "batch_job_rows_rls_and_policies",
        "ALTER TABLE public.batch_job_rows DISABLE ROW LEVEL SECURITY",
    ),
    (
        "20260710000001_review_date.sql",
        "review_date_columns",
        "ALTER TABLE public.extractions DROP COLUMN review_date",
    ),
    (
        "20260710000001_review_date.sql",
        "extractions_review_date_partial_index",
        "DROP INDEX public.idx_extractions_review_date",
    ),
    (
        "20260710235958_capture_extraction_costs.sql",
        "extraction_costs_columns",
        "ALTER TABLE public.extraction_costs DROP COLUMN language",
    ),
    (
        "20260710235958_capture_extraction_costs.sql",
        "extraction_costs_indexes",
        "DROP INDEX public.idx_extraction_costs_language_tier",
    ),
    (
        "20260710235958_capture_extraction_costs.sql",
        "extraction_costs_rls_and_policies",
        "DROP POLICY extraction_costs_anon_deny ON public.extraction_costs",
    ),
    (
        "20260710235959_capture_quota_requests.sql",
        "quota_requests_columns_and_key",
        "ALTER TABLE public.quota_requests DROP COLUMN notes",
    ),
    (
        "20260711000002_quota_requests_rls.sql",
        "quota_requests_rls_and_policies",
        "ALTER TABLE public.quota_requests DISABLE ROW LEVEL SECURITY",
    ),
    (
        "20260711000002_quota_requests_rls.sql",
        "quota_requests_grants_narrowed",
        "GRANT DELETE ON public.quota_requests TO authenticated",
    ),
    (
        "20260731000001_extraction_costs.sql",
        "extraction_costs_foreign_keys_and_checks",
        "ALTER TABLE public.extraction_costs DROP CONSTRAINT extraction_costs_tokens_in_check",
    ),
    (
        "20260731000001_extraction_costs.sql",
        "extraction_costs_rls_and_tenant_policies",
        "ALTER TABLE public.extraction_costs DISABLE ROW LEVEL SECURITY",
    ),
    (
        "20260817000001_extraction_costs_grant_narrowing.sql",
        "extraction_costs_grants_narrowed",
        "GRANT DELETE ON public.extraction_costs TO authenticated",
    ),
]


@pytest.mark.parametrize(
    ("filename", "pc_name", "undo_sql"),
    UNDO_CASES,
    ids=[f"{c[0][:14]}-{c[1]}-{i}" for i, c in enumerate(UNDO_CASES)],
)
def test_postcondition_goes_false_when_the_migrations_effect_is_undone(
    built_db: BuiltDb, filename: str, pc_name: str, undo_sql: str
) -> None:
    pc = _find(filename, pc_name)
    conn = built_db.superuser(autocommit=False)
    try:
        cur = conn.cursor()
        baseline = push.evaluate_postcondition(cur, pc)
        assert baseline.status == "PASS", f"baseline must hold before the undo: {baseline}"
        cur.execute(undo_sql)
        after = push.evaluate_postcondition(cur, pc)
        # FALSE/NULL, not a crash: a condition that merely errors when its object vanishes
        # would still fail closed, but would not prove it discriminates on the effect itself.
        assert after.status == "FAIL", f"vacuous postcondition: still passes after `{undo_sql}`"
        assert "errored" not in after.detail, f"errored instead of evaluating false: {after}"
    finally:
        conn.rollback()
        conn.close()


def test_every_migration_with_postconditions_has_an_undo_case() -> None:
    """A postcondition never shown to go FALSE proves nothing. Session 16 found 17 of 43
    migrations (57 of 92 postconditions) with no undo case at all, behind a ">= 10 distinct
    migrations" threshold that passed regardless. A new migration must now bring its own case."""
    covered = {c[0] for c in UNDO_CASES}
    missing = sorted({fname for fname, _ in _all_postconditions()} - covered)
    assert not missing, f"migrations with postconditions but no UNDO_CASES entry: {missing}"


def test_every_undo_case_names_a_real_postcondition() -> None:
    real = {(fname, pc.name) for fname, pc in _all_postconditions()}
    stale = sorted({(c[0], c[1]) for c in UNDO_CASES} - real)
    assert not stale, f"UNDO_CASES entries naming a postcondition that does not exist: {stale}"


# ---------------------------------------------------------------- 3: the real incident

# 20260912000003's statement, verbatim, plus its postcondition: as review_iq_migrator on a
# postgres-owned function the REVOKE completes with only a WARNING and changes nothing.
INCIDENT_MIGRATION = """\
REVOKE EXECUTE ON FUNCTION public.current_org_id() FROM PUBLIC, anon;

-- @postcondition: anon_cannot_execute_current_org_id
-- SQL: SELECT NOT has_function_privilege('anon', 'public.current_org_id()', 'EXECUTE')
"""


def _anon_can_execute(built_db: BuiltDb) -> bool:
    conn = built_db.superuser()
    try:
        cur = conn.cursor()
        cur.execute("SELECT has_function_privilege('anon', 'public.current_org_id()', 'EXECUTE')")
        (value,) = cur.fetchone()
        return bool(value)
    finally:
        conn.close()


def _ledgered(built_db: BuiltDb, filename: str) -> bool:
    conn = built_db.superuser()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM public._migrations WHERE filename = %s", (filename,))
        (n,) = cur.fetchone()
        return n == 1
    finally:
        conn.close()


def test_non_owner_revoke_is_rejected_by_the_postcondition_and_not_ledgered(
    built_db: BuiltDb, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    filename = "99990101000001_incident_revoke_on_postgres_owned_function.sql"
    path = tmp_path / filename
    path.write_text(INCIDENT_MIGRATION, encoding="utf-8")
    su = built_db.superuser()
    su_cur = su.cursor()
    # Recreate the production condition: the function is owned by postgres, anon can execute
    # it, and the migrator holds EXECUTE without grant option (its 20260801000001
    # `GRANT ALL ON ALL FUNCTIONS`) -- the state in which a non-owner REVOKE is a warning,
    # not an error.
    su_cur.execute("ALTER FUNCTION public.current_org_id() OWNER TO postgres")
    su_cur.execute("GRANT EXECUTE ON FUNCTION public.current_org_id() TO anon")
    su_cur.execute("GRANT EXECUTE ON FUNCTION public.current_org_id() TO review_iq_migrator")
    try:
        assert _anon_can_execute(built_db)

        # As the migrator: the statement "succeeds" but anon can still execute, so the
        # postcondition must reject the file and it must not be ledgered.
        conn = psycopg2.connect(built_db.migrator_dsn, connect_timeout=15)
        conn.autocommit = False
        try:
            ok, applied = push._apply_pending(conn, [path], "ci")
        finally:
            conn.close()
        err = capsys.readouterr().err
        assert (ok, applied) == (False, 0)
        assert f"POSTCONDITION FAILED: {filename} :: anon_cannot_execute_current_org_id" in err
        assert "returned False" in err
        assert _anon_can_execute(built_db), "the REVOKE must really have been a silent no-op"
        assert not _ledgered(built_db, filename)

        # Positive control: the SAME file run as the owner/superuser really revokes, passes its
        # postcondition, and is ledgered -- so the rejection above is about the wall, not a
        # harness that fails everything.
        conn = psycopg2.connect(built_db.super_dsn, connect_timeout=15)
        conn.autocommit = False
        try:
            ok, applied = push._apply_pending(conn, [path], "ci")
        finally:
            conn.close()
        assert (ok, applied) == (True, 1)
        assert not _anon_can_execute(built_db)
        assert _ledgered(built_db, filename)
    finally:
        su_cur.execute("DELETE FROM public._migrations WHERE filename = %s", (filename,))
        su_cur.execute("ALTER FUNCTION public.current_org_id() OWNER TO review_iq_migrator")
        su_cur.execute("REVOKE EXECUTE ON FUNCTION public.current_org_id() FROM anon, PUBLIC")
        su.close()
