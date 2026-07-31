"""Proof that scripts/check_acl_exposure.py actually catches the bug it exists for.

Wave 2 P1: a green audit is worthless if it would stay green even when the exact bug
class it's named for exists. Deliberately creates a disposable, uniquely-named
SECURITY DEFINER function with NO protective revoke -- mimicking the Wave 1 S0 finding
exactly (a webhook-org-resolution-style function inheriting EXECUTE on
anon/authenticated via Supabase's schema-level default ACLs) -- and confirms the audit
flags it. Also proves the audit stays clean on a properly-revoked function and on the
allowlisted `current_org_id`, so this isn't a checker that just always fails either.

Cleans up in `finally` -- the scratch function never survives the test, success or
failure. Matches this repo's own established pattern (test_adversarial_cross_tenant.py,
test_rls_disable_proof.py) of creating small, disposable, uniquely-named real objects
against the live database and tearing them down, rather than mocking the DB layer this
audit specifically needs to run against.

Run explicitly (marked 'integration', skipped in default CI, same convention as this
repo's other live-DB tests):
    uv run pytest tests/integration/test_acl_exposure_audit.py -v -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg2
import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from scripts.check_acl_exposure import run_audit  # noqa: E402 -- needs dotenv loaded first


def _direct_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(os.environ["SUPABASE_DIRECT_URL"])


@pytest.fixture
def vulnerable_function_name() -> Iterator[str]:
    """Creates a disposable SECURITY DEFINER function with NO revoke -- the exact bug
    class -- and drops it unconditionally after the test."""
    name = f"zz_test_acl_vuln_{uuid.uuid4().hex[:8]}"
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE OR REPLACE FUNCTION public.{name}()
            RETURNS text
            LANGUAGE sql
            SECURITY DEFINER
            AS $$ SELECT 'vulnerable, no revoke applied'::text $$
            """
        )
        conn.commit()
        yield name
    finally:
        conn2 = _direct_conn()
        try:
            cur2 = conn2.cursor()
            cur2.execute(f"DROP FUNCTION IF EXISTS public.{name}()")
            conn2.commit()
        finally:
            conn2.close()
        conn.close()


@pytest.fixture
def protected_function_name() -> Iterator[str]:
    """Same shape, but with the correct protective revoke applied -- must NOT be
    flagged. Proves the audit distinguishes protected from unprotected, not just
    "any SECURITY DEFINER function is always flagged"."""
    name = f"zz_test_acl_safe_{uuid.uuid4().hex[:8]}"
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE OR REPLACE FUNCTION public.{name}()
            RETURNS text
            LANGUAGE sql
            SECURITY DEFINER
            AS $$ SELECT 'protected'::text $$
            """
        )
        cur.execute(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC, anon, authenticated")
        conn.commit()
        yield name
    finally:
        conn2 = _direct_conn()
        try:
            cur2 = conn2.cursor()
            cur2.execute(f"DROP FUNCTION IF EXISTS public.{name}()")
            conn2.commit()
        finally:
            conn2.close()
        conn.close()


@pytest.mark.integration
class TestAclExposureAuditCatchesTheBug:
    def test_flags_a_deliberately_unprotected_security_definer_function(
        self, vulnerable_function_name: str
    ) -> None:
        findings = run_audit()
        matching = [f for f in findings if vulnerable_function_name in f]
        assert matching, (
            f"The audit did NOT flag '{vulnerable_function_name}', a deliberately "
            f"unprotected SECURITY DEFINER function (no revoke applied, mimicking the "
            f"exact Wave 1 S0 bug class) -- the checker is not catching the bug it "
            f"exists to catch. All findings: {findings}"
        )

    def test_does_not_flag_a_properly_revoked_security_definer_function(
        self, protected_function_name: str
    ) -> None:
        findings = run_audit()
        matching = [f for f in findings if protected_function_name in f]
        assert not matching, (
            f"The audit flagged '{protected_function_name}', which has an explicit "
            f"REVOKE ALL ... FROM PUBLIC, anon, authenticated already applied -- false "
            f"positive. {matching}"
        )

    def test_current_org_id_allowlisted_and_not_flagged(self) -> None:
        """current_org_id() genuinely needs authenticated EXECUTE for RLS to function
        -- must be allowlisted, not flagged, and this must stay true (a regression
        here would mean someone deleted the allowlist entry without understanding why
        it exists, or renamed the function without updating it)."""
        findings = run_audit()
        matching = [f for f in findings if "current_org_id" in f]
        assert not matching, (
            f"current_org_id() was flagged despite being on the explicit allowlist in "
            f"scripts/check_acl_exposure.py -- either the allowlist logic broke, or "
            f"someone removed the entry. Findings: {matching}"
        )
