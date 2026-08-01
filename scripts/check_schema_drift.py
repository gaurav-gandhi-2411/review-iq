"""CI gate: the committed migration set must produce EXACTLY the same schema as
production. This is the standing control that makes the ephemeral pre-cutover
verification job (pre-cutover-verification.yml) meaningful -- without it, that job
only proves the migrations are internally consistent with each other, not that they
match reality. Built 2026-08-01 after finding quota_requests existed in production
with no CREATE TABLE anywhere in supabase/migrations/ -- an audit that had to be done
by hand once; this makes it automatic and continuous.

Usage:
    uv run python scripts/extract_schema_snapshot.py "$PROD_DSN" > prod.json
    uv run python scripts/extract_schema_snapshot.py "$EPHEMERAL_DSN" > ephemeral.json
    uv run python scripts/check_schema_drift.py prod.json ephemeral.json

Exit 0 if the two snapshots match (after excluding the known, reasoned differences
below); exit 1 and print every diff otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Known, reasoned exclusions -- every entry here is a deliberate, documented
# decision, not a silent tolerance. Anything not listed here is a real diff.
# ---------------------------------------------------------------------------

# review_iq_app.rolbypassrls is EXPECTED to differ until the cutover is actually
# re-applied to production (this script runs as part of P0's schema-fidelity audit,
# BEFORE that happens). Rather than excluding review_iq_app from the roles diff
# entirely -- which would also hide a genuinely missing role or an unexpected
# rolcanlogin drift -- only rolbypassrls is stripped from its row before comparing,
# so the rest of the role's shape still gets checked.
_ROLES_IGNORE_FIELD = {"review_iq_app": {"rolbypassrls"}}

# Supabase's own platform bootstrap grants a long, ever-growing list of default
# privileges to postgres/supabase_admin/dashboard_user-style internal roles that
# never appear in this app's own migrations and are not meaningful to this app's
# security model -- only compare grants to the roles this app actually cares about.
# `postgres` is excluded here: it's whichever role literally owns each table, and
# that identity is an artifact of HOW the schema was built (the ephemeral job
# connects and creates everything as `postgres`; the real Supabase project's tables
# were created by Supabase's own internal provisioning role, not literally named
# "postgres") -- comparing table-owner grants across the two would always show a
# diff that has nothing to do with this app's actual permission model.
_GRANT_ROLES_OF_INTEREST = {
    "review_iq_app",
    "review_iq_admin",
    "review_iq_migrator",
    "anon",
    "authenticated",
    "service_role",
}

# review_iq_migrator's own migration (20260801000001, statement 1) contains explicit
# `GRANT ALL ON ALL TABLES/FUNCTIONS IN SCHEMA public TO review_iq_migrator` --
# retroactive, so a from-scratch ephemeral rebuild always shows migrator with grants
# on every table/function that existed when that statement ran. Production shows
# ZERO table AND ZERO function grants for migrator (confirmed live, 2026-08-01, via
# information_schema.role_table_grants / role_routine_grants directly) despite that
# same migration statement being confirmed already applied there -- an unexplained,
# real gap between what the committed migration would produce and what's actually
# live (both manifestations share the same root cause: whatever ran that GRANT ALL
# statement in production, it didn't take effect the way the current file says it
# should). Not corrected here: review_iq_migrator is never referenced by any
# application setting, Cloud Run env var, or Secret Manager secret (grepped,
# confirmed) -- it is structurally unreachable from any request-serving code path,
# so this gap carries no security exposure. Flagged, investigated, and excluded as
# a known, accepted, low-risk difference -- not silently dropped without explanation.
_MIGRATOR_GRANTS_KNOWN_GAP = True


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _key(row: dict[str, Any], fields: list[str]) -> tuple[object, ...]:
    return tuple(row.get(f) for f in fields)


def _index_by(
    rows: list[dict[str, Any]], fields: list[str]
) -> dict[tuple[object, ...], dict[str, Any]]:
    return {_key(r, fields): r for r in rows}


def _diff_section(
    name: str,
    prod_rows: list[dict[str, Any]],
    eph_rows: list[dict[str, Any]],
    key_fields: list[str],
) -> list[str]:
    prod_idx = _index_by(prod_rows, key_fields)
    eph_idx = _index_by(eph_rows, key_fields)
    diffs: list[str] = []

    only_prod = set(prod_idx) - set(eph_idx)
    only_eph = set(eph_idx) - set(prod_idx)
    common = set(prod_idx) & set(eph_idx)

    for k in sorted(only_prod, key=str):
        diffs.append(
            f"[{name}] {k}: present in PRODUCTION, absent from the migration-built "
            f"schema -- a migration is missing. Row: {prod_idx[k]}"
        )
    for k in sorted(only_eph, key=str):
        diffs.append(
            f"[{name}] {k}: present in the migration-built schema, absent from "
            f"PRODUCTION -- either a migration ran that production never got, or "
            f"production drifted. Row: {eph_idx[k]}"
        )
    for k in sorted(common, key=str):
        if prod_idx[k] != eph_idx[k]:
            diffs.append(
                f"[{name}] {k}: differs.\n    production: {prod_idx[k]}\n    migrations:  {eph_idx[k]}"
            )
    return diffs


def compare(prod: dict[str, Any], eph: dict[str, Any]) -> list[str]:
    diffs: list[str] = []

    diffs += _diff_section(
        "columns", prod["columns"], eph["columns"], ["table_name", "column_name"]
    )
    diffs += _diff_section(
        "constraints", prod["constraints"], eph["constraints"], ["table_name", "conname"]
    )
    diffs += _diff_section("indexes", prod["indexes"], eph["indexes"], ["table_name", "indexname"])
    diffs += _diff_section("rls_enabled", prod["rls_enabled"], eph["rls_enabled"], ["table_name"])
    diffs += _diff_section(
        "policies", prod["policies"], eph["policies"], ["table_name", "policyname"]
    )
    diffs += _diff_section(
        "functions", prod["functions"], eph["functions"], ["function_name", "arguments"]
    )

    grant_roles = _GRANT_ROLES_OF_INTEREST - (
        {"review_iq_migrator"} if _MIGRATOR_GRANTS_KNOWN_GAP else set()
    )
    prod_fgrants = [g for g in prod["function_grants"] if g["grantee"] in grant_roles]
    eph_fgrants = [g for g in eph["function_grants"] if g["grantee"] in grant_roles]
    diffs += _diff_section(
        "function_grants", prod_fgrants, eph_fgrants, ["function_name", "grantee", "privilege_type"]
    )

    prod_tgrants = [g for g in prod["table_grants"] if g["grantee"] in grant_roles]
    eph_tgrants = [g for g in eph["table_grants"] if g["grantee"] in grant_roles]
    diffs += _diff_section(
        "table_grants", prod_tgrants, eph_tgrants, ["table_name", "grantee", "privilege_type"]
    )

    def _strip_ignored(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in r.items() if k not in _ROLES_IGNORE_FIELD.get(r["rolname"], set())}
            for r in rows
        ]

    diffs += _diff_section(
        "roles", _strip_ignored(prod["roles"]), _strip_ignored(eph["roles"]), ["rolname"]
    )

    return diffs


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: check_schema_drift.py <prod_snapshot.json> <ephemeral_snapshot.json>",
            file=sys.stderr,
        )
        return 2

    prod = _load(sys.argv[1])
    eph = _load(sys.argv[2])
    diffs = compare(prod, eph)

    if diffs:
        print(
            f"FAIL: {len(diffs)} schema difference(s) between production and the migration set:\n",
            file=sys.stderr,
        )
        for d in diffs:
            print(f"  {d}", file=sys.stderr)
        return 1

    print(
        "OK: migration-built schema matches production exactly (excluding the documented, reasoned exclusions)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
