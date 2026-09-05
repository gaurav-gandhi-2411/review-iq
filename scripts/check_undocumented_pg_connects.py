"""CI guard: every psycopg2 connect-and-query function must be tenant-scoped.

Built 2026-08-01 after an audit (BYPASSRLS remediation, pass 2c) found several
call sites quietly relying on review_iq_app's BYPASSRLS grant to reach correct
rows -- the _set_tenant() convention documented in app/core/storage_pg.py's
module docstring was not actually applied everywhere it should have been, and
nothing caught the gap until it was found by eye.

This guard does not understand SQL -- it cannot verify a WHERE clause is
correct, and it is not a substitute for the adversarial RLS integration tests
in tests/integration/test_adversarial_cross_tenant.py. It verifies exactly one
structural property: every function that opens a psycopg2 connection AND
issues at least one query must either

  (a) call _set_tenant() somewhere in its own body, or
  (b) be explicitly named in ALLOWLIST below, with a one-line reason.

Anything else fails the guard. The fix is one of:
  - wire in _set_tenant() (org_id is already a known parameter -- mechanical), or
  - resolve org_id via a narrow SECURITY DEFINER function first, then
    _set_tenant() (org unknown until the query resolves it -- see
    app/api/webhooks/google.py::_get_google_installation_pg for the pattern), or
  - add a reasoned ALLOWLIST entry (genuine cross-org/service-role bypass, e.g.
    a scheduled sweep or the separate admin-role connection).

A pure connection factory (a function whose body has no `.cursor(`/`cur.execute`
of its own -- e.g. `_db_connect() -> return psycopg2.connect(...)`) is not a
call site itself and is never flagged; the guard follows local calls to these
factories transparently.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"

# Whole files exempt from this guard entirely -- reviewed via normal PR review, same
# spirit as the per-function ALLOWLIST below.
FILE_ALLOWLIST: dict[str, str] = {
    "app/api/admin.py": (
        "Every function in this module connects via review_iq_admin (BYPASSRLS by "
        "design, ADR 0006) -- this module only runs inside the private, IAM-gated "
        "review-iq-admin Cloud Run service, never the public review_iq_app path this "
        "guard exists to protect."
    ),
}

# (relative file path, function name) -> reason. Reviewed via normal PR review, same
# spirit as merge_gate.py's DESIGNATED_PATTERNS carve-out list in claude-config.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("app/auth/signup.py", "_provision_org_and_key"): (
        "Org doesn't exist yet -- there is nothing to _set_tenant() to. Both writes go "
        "through public.create_org_and_membership() / public.create_api_key_for_org(), "
        "narrow SECURITY DEFINER functions (see supabase/migrations/"
        "20260801000002_tenant_resolvers_auth_signup.sql) -- review_iq_app itself holds "
        "no direct INSERT grant on organizations/organization_members/api_keys."
    ),
    ("app/core/storage_pg.py", "list_orgs_with_dated_extractions_pg"): (
        "Documented cross-org scheduled-sweep query (detector_sweep.py) -- there is no "
        "single org_id to scope to for a query that must see every org."
    ),
    ("app/core/alerts/storage.py", "list_orgs_with_daily_digest_pg"): (
        "Documented cross-org scheduled-sweep query (digest batcher) -- same pattern as "
        "storage_pg.py::list_orgs_with_dated_extractions_pg."
    ),
    ("app/core/ingest_worker.py", "_claim_one_row"): (
        "Cross-org queue-drain claim (batch_job_rows) -- goes through public."
        "claim_pending_batch_job_row() / public.settle_batch_job_row(), narrow SECURITY "
        "DEFINER functions (see supabase/migrations/"
        "20260801000002_tenant_resolvers_auth_signup.sql), since no per-org RLS policy "
        "can express 'see pending rows across every org'. The extraction itself is "
        "attributed per-row via a fresh ApiKeyContext into _set_tenant()-scoped "
        "storage_pg functions downstream."
    ),
    ("app/api/ops.py", "_sync_ping"): (
        "SELECT 1 only -- no table access, RLS/tenant-scoping is not applicable."
    ),
    ("app/api/google_auth.py", "_upsert_installation_pg"): (
        "authenticated holds only a SELECT policy on google_business_installations "
        "(20260702000001_google_business_installations.sql) -- a _set_tenant()-only "
        "fix does not work for this INSERT/UPDATE. Goes through public."
        "upsert_google_installation(), a narrow SECURITY DEFINER function (see "
        "supabase/migrations/20260801000002_tenant_resolvers_auth_signup.sql)."
    ),
    ("app/api/shopify_auth.py", "_upsert_installation_pg"): (
        "authenticated holds only a SELECT policy on shopify_installations "
        "(20260622000001_shopify_installations.sql) -- a _set_tenant()-only fix does "
        "not work for this INSERT/UPDATE. Goes through public."
        "upsert_shopify_installation(), a narrow SECURITY DEFINER function (see "
        "supabase/migrations/20260801000002_tenant_resolvers_auth_signup.sql)."
    ),
    ("app/core/storage_pg.py", "aggregate_extraction_costs_pg"): (
        "Platform-wide COGS aggregate across all orgs -- same intentional cross-org "
        "service-role bypass pattern as list_orgs_with_dated_extractions_pg above. "
        "Cherry-picked from unmerged PR #24 alongside the cost-recording helpers this "
        "PR wires into /demo/extract; PR #24's own require_admin-gated endpoint that "
        "calls this function was NOT included here (out of scope for a demo-endpoint "
        "safety PR) -- this function is currently only reachable in tests until that "
        "endpoint lands separately."
    ),
    ("app/core/storage_pg.py", "check_and_increment_demo_request_pg"): (
        "POST /demo/extract is keyless -- there is no org to _set_tenant() to. Writes "
        "only public.demo_daily_usage, a single global (non-tenant) counter table with "
        "no RLS, grant-scoped to review_iq_app only (see "
        "supabase/migrations/20260905000001_demo_daily_usage.sql)."
    ),
    ("app/core/storage_pg.py", "record_demo_extraction_cost_pg"): (
        "POST /demo/extract is keyless -- there is no org to _set_tenant() to. Inserts "
        "org_id=NULL, source='demo' rows into extraction_costs, permitted by a policy "
        "scoped specifically to review_iq_app (not anon/authenticated), see "
        "supabase/migrations/20260905000002_extraction_costs_allow_demo_rows.sql -- "
        "authenticated tenants can never see these rows (NULL org_id never equals any "
        "real current_org_id())."
    ),
}

_CONNECT_ATTR = "connect"
_SET_TENANT_NAME = "_set_tenant"


def _walk_own_body(func_node: ast.AST) -> list[ast.AST]:
    """Like ast.walk(func_node), but does not descend into nested function defs --
    a nested function's own connect/cursor/_set_tenant calls belong to ITS analysis,
    not its enclosing function's (otherwise e.g. app/api/ops.py's async
    _ping_postgres would be credited -- or blamed -- for its nested _sync_ping
    closure's psycopg2.connect() call)."""
    result: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(func_node))
    while stack:
        node = stack.pop()
        result.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # do not descend into nested function bodies
        stack.extend(ast.iter_child_nodes(node))
    return result


def _is_psycopg2_connect_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == _CONNECT_ATTR:
        value = func.value
        if isinstance(value, ast.Name) and "psycopg2" in value.id:
            return True
    return False


def _calls_name(func_node: ast.AST, name: str) -> bool:
    for node in _walk_own_body(func_node):
        if isinstance(node, ast.Call):
            called = node.func
            if isinstance(called, ast.Name) and called.id == name:
                return True
            if isinstance(called, ast.Attribute) and called.attr == name:
                return True
    return False


def _has_cursor_usage(func_node: ast.AST) -> bool:
    for node in _walk_own_body(func_node):
        if isinstance(node, ast.Attribute) and node.attr in ("execute", "cursor"):
            return True
    return False


def _connects_directly_or_via_factory(func_node: ast.AST, factory_names: set[str]) -> bool:
    for node in _walk_own_body(func_node):
        if isinstance(node, ast.Call):
            if _is_psycopg2_connect_call(node):
                return True
            called = node.func
            if isinstance(called, ast.Name) and called.id in factory_names:
                return True
    return False


def _find_factory_names(tree: ast.Module) -> set[str]:
    """A pure factory: a function with no cursor/execute usage of its own that directly
    calls psycopg2.connect(...) somewhere in its body (typically a bare `return
    psycopg2.connect(dsn)`, but tolerant of a couple of statements around it)."""
    factories: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _has_cursor_usage(node):
                continue
            for inner in _walk_own_body(node):
                if isinstance(inner, ast.Call) and _is_psycopg2_connect_call(inner):
                    factories.add(node.name)
                    break
    return factories


def check_file(path: Path, factory_names: set[str]) -> list[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel in FILE_ALLOWLIST:
        return []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel)

    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in factory_names:
            continue  # the factory itself issues no queries -- nothing to scope
        if not _has_cursor_usage(node):
            continue  # opens no cursor -- not a real call site (e.g. a pure helper)
        if not _connects_directly_or_via_factory(node, factory_names):
            continue  # doesn't open a DB connection at all -- not in scope for this guard
        if _calls_name(node, _SET_TENANT_NAME):
            continue  # tenant-scoped -- OK
        if (rel, node.name) in ALLOWLIST:
            continue  # explicit, reasoned exemption -- OK
        failures.append(
            f"{rel}:{node.lineno}: {node.name}() opens a psycopg2 connection and issues "
            f"queries, but never calls _set_tenant() and is not in ALLOWLIST."
        )
    return failures


def main() -> int:
    paths = sorted(APP_DIR.rglob("*.py"))

    # Factory names must be collected GLOBALLY, across every file, before checking any
    # single file's call sites -- a factory function (e.g. _db_connect) can be defined
    # in one module and imported/called from another (see app/api/account.py importing
    # app.auth.signup._db_connect). Building this set per-file (the original design)
    # missed exactly that case: account.py's own AST never defines _db_connect, so its
    # calls to the imported function were invisible to a per-file factory scan --
    # meaning account.py's real, undocumented connect sites went uncaught. Found
    # 2026-08-01 while building the pre-cutover ephemeral-Postgres CI job (P3): a
    # concrete instance of rule 85a's own failure class (a control that is real,
    # passing, and green, while quietly covering less than its name implies).
    factory_names: set[str] = set()
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.relative_to(REPO_ROOT).as_posix())
        factory_names |= _find_factory_names(tree)

    all_failures: list[str] = []
    for path in paths:
        all_failures.extend(check_file(path, factory_names))

    if all_failures:
        print("FAIL: undocumented / unscoped psycopg2 connect sites found:\n", file=sys.stderr)
        for line in all_failures:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nFix: wire in _set_tenant(), add a SECURITY DEFINER resolver + _set_tenant() "
            "(see app/api/webhooks/google.py::_get_google_installation_pg), or add a "
            "reasoned ALLOWLIST entry in scripts/check_undocumented_pg_connects.py.",
            file=sys.stderr,
        )
        return 1

    print("OK: every psycopg2 connect-and-query call site is tenant-scoped or allowlisted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
