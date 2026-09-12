"""Unit tests for scripts/check_schema_drift.py.

Session 6 P5d: this script had zero test coverage before the schema-drift-check.yml
root-cause fix (service_role grants + the _migrations table exclusion), both added the
same session. These tests cover the exclusion logic and the overall compare() shape.
"""

from __future__ import annotations

from scripts.check_schema_drift import _without_migrations_table, compare


def _empty_snapshot() -> dict:
    return {
        "columns": [],
        "constraints": [],
        "indexes": [],
        "rls_enabled": [],
        "policies": [],
        "functions": [],
        "function_grants": [],
        "table_grants": [],
        "roles": [],
    }


class TestWithoutMigrationsTable:
    def test_drops_migrations_rows(self):
        rows = [
            {"table_name": "_migrations", "column_name": "filename"},
            {"table_name": "extractions", "column_name": "id"},
        ]
        out = _without_migrations_table(rows)
        assert out == [{"table_name": "extractions", "column_name": "id"}]

    def test_empty_list_stays_empty(self):
        assert _without_migrations_table([]) == []

    def test_no_migrations_rows_unaffected(self):
        rows = [{"table_name": "extractions", "column_name": "id"}]
        assert _without_migrations_table(rows) == rows


class TestCompareIgnoresMigrationsTable:
    def test_migrations_only_in_production_produces_no_diff(self):
        # Real shape found live (Session 6 P5d): Supabase's own internal
        # migration-tracking table exists in production with real columns/grants but
        # is never created by anything in supabase/migrations/, so the ephemeral
        # rebuild never has it. This must not be reported as drift.
        prod = _empty_snapshot()
        prod["columns"] = [{"table_name": "_migrations", "column_name": "filename"}]
        prod["table_grants"] = [
            {"table_name": "_migrations", "grantee": "service_role", "privilege_type": "SELECT"}
        ]
        eph = _empty_snapshot()
        diffs = compare(prod, eph)
        assert diffs == []

    def test_real_drift_on_a_non_migrations_table_still_reported(self):
        prod = _empty_snapshot()
        prod["columns"] = [{"table_name": "extraction_costs", "column_name": "cost_usd"}]
        eph = _empty_snapshot()
        diffs = compare(prod, eph)
        assert len(diffs) == 1
        assert "extraction_costs" in diffs[0]

    def test_service_role_table_grant_present_both_sides_produces_no_diff(self):
        # Regression coverage for the actual root-cause fix: service_role now genuinely
        # has table grants in production (verified live) and bootstrap_supabase_roles.sql
        # was updated to grant the same in the ephemeral job -- both sides matching means
        # zero diff, not the ~130-diff false positive this used to produce.
        prod = _empty_snapshot()
        eph = _empty_snapshot()
        grant = {
            "table_name": "extraction_costs",
            "grantee": "service_role",
            "privilege_type": "SELECT",
        }
        prod["table_grants"] = [grant]
        eph["table_grants"] = [dict(grant)]
        assert compare(prod, eph) == []

    def test_service_role_table_grant_missing_from_ephemeral_is_a_real_diff(self):
        prod = _empty_snapshot()
        eph = _empty_snapshot()
        prod["table_grants"] = [
            {
                "table_name": "extraction_costs",
                "grantee": "service_role",
                "privilege_type": "SELECT",
            }
        ]
        diffs = compare(prod, eph)
        assert len(diffs) == 1
        assert "service_role" in diffs[0]
