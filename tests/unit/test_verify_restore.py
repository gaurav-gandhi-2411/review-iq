from __future__ import annotations

from collections import Counter

from scripts.verify_restore import compare, parse_dump, roles_referenced

DUMP = [
    "CREATE TABLE public.organizations (",
    "    id uuid",
    ");",
    'CREATE TABLE public."api_keys" (',
    ");",
    "ALTER TABLE public.organizations OWNER TO postgres;",
    "COPY public.organizations (id, name) FROM stdin;",
    "1\tAcme",
    "2\tBeta\\nline",
    "\\.",
    "ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;",
    "CREATE POLICY org_iso ON public.organizations TO authenticated USING (true);",
    "GRANT SELECT ON TABLE public.organizations TO anon, service_role;",
    "ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public GRANT ALL ON TABLES TO anon;",
    "REVOKE ALL ON FUNCTION public.f() FROM PUBLIC;",
    "COPY public.api_keys (id) FROM stdin;",
    "x\tGRANT ALL ON y TO should_not_count;",
    "\\.",
]


def test_parse_dump_counts_rows_policies_rls() -> None:
    p = parse_dump(DUMP)
    assert p["tables"] == {"organizations", "api_keys"}
    assert p["rows"] == Counter({"organizations": 2, "api_keys": 1})
    assert (p["rls"], p["policies"]) == (1, 1)


def test_roles_found_but_copy_data_ignored() -> None:
    assert roles_referenced(DUMP) == ["anon", "authenticated", "service_role", "supabase_admin"]


def test_compare_passes_on_match_and_flags_each_failure_kind() -> None:
    p = parse_dump(DUMP)
    ok = compare(p, {"organizations": 2, "api_keys": 1}, 1, 1, "organizations")
    assert ok == []
    bad = compare(p, {"organizations": 1, "extra": 0}, 0, 0, "organizations")
    text = " | ".join(bad)
    assert "api_keys is in the dump but missing" in text
    assert "extra exists after restore" in text
    assert "dump has 2 rows, restored 1" in text
    assert "RLS-enabled tables" in text and "policies" in text


def test_empty_dump_and_empty_organizations_fail() -> None:
    assert compare(parse_dump([]), {}, 0, 0, "organizations")
    empty_org = ["CREATE TABLE public.organizations (", ");"]
    assert any(
        "0 rows" in x
        for x in compare(parse_dump(empty_org), {"organizations": 0}, 0, 0, "organizations")
    )
