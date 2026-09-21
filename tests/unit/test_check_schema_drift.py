"""Unit tests for scripts/check_schema_drift.py.

Session 6 P5d: this script had zero test coverage before the schema-drift-check.yml
root-cause fix (service_role grants + the _migrations table exclusion), both added the
same session. These tests cover the exclusion logic and the overall compare() shape.
"""

from __future__ import annotations

import pytest
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
        "relation_owners": [],
        "function_owners": [],
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


class TestObjectOwners:
    """Session 15d: a non-owner GRANT/REVOKE silently changes nothing, so owner drift matters."""

    def test_relation_owner_mismatch_is_reported(self):
        prod = _empty_snapshot()
        eph = _empty_snapshot()
        prod["relation_owners"] = [{"table_name": "leads", "kind": "r", "owner": "postgres"}]
        eph["relation_owners"] = [
            {"table_name": "leads", "kind": "r", "owner": "review_iq_migrator"}
        ]
        diffs = compare(prod, eph)
        assert len(diffs) == 1
        assert "relation_owners" in diffs[0]
        assert "postgres" in diffs[0]

    def test_function_owner_mismatch_is_reported(self):
        prod = _empty_snapshot()
        eph = _empty_snapshot()
        row = {"function_name": "current_org_id", "arguments": ""}
        prod["function_owners"] = [{**row, "owner": "postgres"}]
        eph["function_owners"] = [{**row, "owner": "review_iq_migrator"}]
        diffs = compare(prod, eph)
        assert len(diffs) == 1
        assert "function_owners" in diffs[0]

    def test_matching_owners_produce_no_diff(self):
        prod = _empty_snapshot()
        eph = _empty_snapshot()
        rel = {"table_name": "leads", "kind": "r", "owner": "review_iq_migrator"}
        fn = {"function_name": "f", "arguments": "uuid", "owner": "review_iq_migrator"}
        prod["relation_owners"], eph["relation_owners"] = [rel], [dict(rel)]
        prod["function_owners"], eph["function_owners"] = [fn], [dict(fn)]
        assert compare(prod, eph) == []

    def test_object_missing_on_one_side_is_reported(self):
        prod = _empty_snapshot()
        eph = _empty_snapshot()
        prod["relation_owners"] = [
            {"table_name": "leads", "kind": "r", "owner": "review_iq_migrator"}
        ]
        assert len(compare(prod, eph)) == 1

    def test_snapshot_from_an_old_extractor_fails_closed(self):
        prod = _empty_snapshot()
        del prod["function_owners"]
        with pytest.raises(ValueError, match="function_owners"):
            compare(prod, _empty_snapshot())


class TestEmptySnapshotsAreRefused:
    """Two empty snapshots diff as identical; the gate must not report that as a match."""

    def _write(self, tmp_path, name, snapshot):
        import json

        path = tmp_path / name
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return str(path)

    def test_compare_alone_cannot_tell_empty_from_matching(self):
        # Pins the hole: this is why main() needs snapshot_problems().
        assert compare(_empty_snapshot(), _empty_snapshot()) == []

    def test_snapshot_problems_flags_every_core_section_when_empty(self):
        from scripts.check_schema_drift import _MUST_BE_NON_EMPTY, snapshot_problems

        assert len(snapshot_problems("x", _empty_snapshot())) == len(_MUST_BE_NON_EMPTY)

    def test_main_returns_1_for_two_empty_snapshots(self, tmp_path, monkeypatch, capsys):
        import scripts.check_schema_drift as mod

        a = self._write(tmp_path, "prod.json", _empty_snapshot())
        b = self._write(tmp_path, "eph.json", _empty_snapshot())
        monkeypatch.setattr("sys.argv", ["check_schema_drift.py", a, b])
        assert mod.main() == 1
        assert "empty snapshot proves nothing" in capsys.readouterr().err

    def test_main_returns_0_for_two_identical_populated_snapshots(self, tmp_path, monkeypatch):
        import scripts.check_schema_drift as mod

        snap = _empty_snapshot()
        snap["columns"] = [{"table_name": "t", "column_name": "id"}]
        snap["constraints"] = [{"table_name": "t", "conname": "t_pkey"}]
        snap["indexes"] = [{"table_name": "t", "indexname": "t_pkey"}]
        snap["rls_enabled"] = [{"table_name": "t", "rls_enabled": True}]
        snap["policies"] = [{"table_name": "t", "policyname": "p"}]
        snap["functions"] = [{"function_name": "f", "arguments": ""}]
        snap["roles"] = [{"rolname": "review_iq_app"}]
        a = self._write(tmp_path, "prod.json", snap)
        b = self._write(tmp_path, "eph.json", snap)
        monkeypatch.setattr("sys.argv", ["check_schema_drift.py", a, b])
        assert mod.main() == 0
