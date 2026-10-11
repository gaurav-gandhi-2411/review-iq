#!/usr/bin/env bash
# Rehearsal for ci_readonly_role.sql against a throwaway, migration-built Postgres (never prod).
# Env: ADMIN_DSN (superuser), RO_DSN (ci_readonly), after the role script has been applied.
set -u
fail=0
ok()   { echo "  [ok] $1"; }
bad()  { echo "::error::$1"; fail=1; }
must_succeed() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "should succeed: $d"; fi; }
must_fail()    { local d="$1"; shift; if "$@" >/dev/null 2>&1; then bad "should FAIL: $d"; else ok "denied: $d"; fi; }
ro() { psql "$RO_DSN" -v ON_ERROR_STOP=1 -qtAc "$1"; }

psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -qc "INSERT INTO public.organizations (name, slug) VALUES ('rehearsal', 'rehearsal-ro') ON CONFLICT DO NOTHING"
must_succeed "SELECT organizations" ro "SELECT 1 FROM public.organizations LIMIT 1"
if [ "$(ro "SELECT count(*) FROM public.organizations")" -ge 1 ]; then ok "RLS table rows visible (BYPASSRLS)"; else bad "RLS table shows 0 rows"; fi
must_succeed "SELECT public._migrations" ro "SELECT count(*) FROM public._migrations"
must_fail "INSERT" ro "INSERT INTO public.organizations (name, slug) VALUES ('x', 'x-ro')"
must_fail "UPDATE" ro "UPDATE public.organizations SET name = 'y'"
must_fail "DELETE" ro "DELETE FROM public.organizations"
must_fail "TRUNCATE" ro "TRUNCATE public.organizations"
must_fail "CREATE TABLE in public" ro "CREATE TABLE public.ro_probe (i int)"
must_fail "CREATE SCHEMA" ro "CREATE SCHEMA ro_probe"
must_fail "DROP TABLE" ro "DROP TABLE public.organizations"
must_fail "ALTER TABLE" ro "ALTER TABLE public.organizations ADD COLUMN ro_probe int"
must_fail "CREATE ROLE" ro "CREATE ROLE ro_probe_role"
must_fail "ALTER ROLE self to SUPERUSER" ro "ALTER ROLE ci_readonly SUPERUSER"
must_fail "GRANT" ro "GRANT ALL ON public.organizations TO PUBLIC"
psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -qc "CREATE SEQUENCE public.ro_seq"
must_succeed "SELECT on sequence" ro "SELECT last_value FROM public.ro_seq"
must_fail "nextval (sequence write)" ro "SELECT nextval('public.ro_seq')"
psql "$ADMIN_DSN" -qc "DROP SEQUENCE public.ro_seq"

# A table created AFTER the role script (as the superuser) must be readable: default privileges.
psql "$ADMIN_DSN" -v ON_ERROR_STOP=1 -qc "CREATE TABLE public.ro_future (i int); INSERT INTO public.ro_future VALUES (1)"
must_succeed "SELECT on table created later (default privileges)" ro "SELECT i FROM public.ro_future"
psql "$ADMIN_DSN" -qc "DROP TABLE public.ro_future"

# The backup path: pg_dump 17 as ci_readonly must contain the seeded organization row.
# To a file, not a pipe into grep -q: an early grep exit gives pg_dump a spurious EPIPE.
if pg_dump --no-password --clean --if-exists --schema=public "$RO_DSN" > /tmp/ro_dump.sql && grep -q "rehearsal-ro" /tmp/ro_dump.sql; then
  ok "pg_dump as ci_readonly includes row data"
else
  bad "pg_dump as ci_readonly is incomplete or failed"
fi

# Snapshot parity: the drift extractor must see the same grants/owners/policies as the superuser.
uv run python scripts/extract_schema_snapshot.py "$ADMIN_DSN" > /tmp/snap_admin.json
uv run python scripts/extract_schema_snapshot.py "$RO_DSN" > /tmp/snap_ro.json
python3 - <<'PY' || fail=1
import json
a, b = json.load(open("/tmp/snap_admin.json")), json.load(open("/tmp/snap_ro.json"))
bad = [k for k in a if a[k] != b.get(k)]
print("snapshot sections differing:", bad or "none")
raise SystemExit(1 if bad else 0)
PY
exit $fail
