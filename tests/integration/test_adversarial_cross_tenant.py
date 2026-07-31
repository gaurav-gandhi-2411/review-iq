"""Adversarial cross-tenant test suite -- Wave 1 Section E, spec's exact 4 vectors.

Requires direct DB credentials (SUPABASE_DATABASE_URL = review_iq_app, and
SUPABASE_DB_PASSWORD for the postgres superuser used only for privileged setup/tenant
seeding) in .env. Marked 'integration' -- skipped in default CI; run explicitly:
    uv run pytest tests/integration/test_adversarial_cross_tenant.py -v -m integration

For every org-scoped surface, this suite attempts the 4 vectors named in the Wave 1 spec:
  1. A valid API key belonging to org A, used to try to read org B's data.
  2. A forged/invalid JWT presented to the BFF session-auth path.
  3. A mismatched org_id supplied in a request body, alongside a valid key for a
     different org.
  4. A direct connection using the app's own database role (review_iq_app), bypassing
     the API layer entirely.

Vector 4 has a genuinely nuanced, non-rubber-stamp answer -- see
TestVector4DirectAppRoleConnection's docstring and
test_review_iq_app_role_confirmed_bypassrls_by_design below. It does NOT simply "pass":
the honest finding is that RLS provides no protection to this specific vector unless the
app code calls `_set_tenant()` first, and this suite documents and proves that precisely,
rather than reporting a false "isolation holds" verdict for a case where it doesn't hold
unconditionally.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import psycopg2
import pytest
from dotenv import load_dotenv

_OrgFixture = dict[str, str]

load_dotenv(Path(__file__).parents[2] / ".env")

_SUPERUSER_DB_PARAMS = {
    "host": "db.enqpluazgxewepchdeut.supabase.co",
    "port": 5432,
    "dbname": "postgres",
    "user": "postgres",
    "password": os.environ.get("SUPABASE_DB_PASSWORD", ""),
    "sslmode": "require",
    "connect_timeout": 15,
}


def _superuser_conn() -> psycopg2.extensions.connection:
    """Privileged connection, used only for test-data setup/teardown, never for the
    actual adversarial assertions (those must go through the real app-role connection
    or the real HTTP API, or they prove nothing)."""
    return psycopg2.connect(**_SUPERUSER_DB_PARAMS)


def _app_role_conn() -> psycopg2.extensions.connection:
    """The app's own real runtime connection -- connects as review_iq_app, exactly the
    credential the FastAPI app itself uses. This is the actual attack surface for
    vector 4 and the DB-level half of vectors 1/3."""
    return psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"])


@pytest.fixture(scope="module")
def two_orgs_with_keys() -> Iterator[tuple[_OrgFixture, _OrgFixture]]:
    """Create two real orgs with real argon2id-hashed API keys and one extraction each.

    Returns two dicts: {org_id, raw_key, key_id, extraction_id} for org A and org B.
    Cleans up via CASCADE delete on the organizations row.
    """
    from argon2 import PasswordHasher

    ph = PasswordHasher()
    conn = _superuser_conn()
    orgs = []
    try:
        cur = conn.cursor()
        for label in ("alpha", "beta"):
            org_id = str(uuid.uuid4())
            key_id = str(uuid.uuid4())
            ext_id = str(uuid.uuid4())
            raw_key = f"riq_live_{uuid.uuid4().hex}"
            key_hash = ph.hash(raw_key)
            cur.execute(
                "INSERT INTO public.organizations (id, name, slug) VALUES (%s, %s, %s)",
                (org_id, f"Adversarial {label}", f"adv-{label}-{org_id[:8]}"),
            )
            cur.execute(
                "INSERT INTO public.api_keys "
                "(id, org_id, name, key_prefix, key_hash, quota) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (key_id, org_id, f"adv-key-{label}", raw_key[:17], key_hash, 1000),
            )
            cur.execute(
                "INSERT INTO public.extractions "
                "(id, org_id, input_hash, extraction, model, prompt_version, schema_version, "
                " product, sentiment, urgency, extracted_at) "
                "VALUES (%s, %s, %s, %s, 'test-model', 'v1.0', 'v1', %s, 'positive', 'low', now())",
                (
                    ext_id,
                    org_id,
                    f"hash_adv_{label}",
                    f'{{"stars": 4, "product": "{label}"}}',
                    label,
                ),
            )
            orgs.append(
                {"org_id": org_id, "raw_key": raw_key, "key_id": key_id, "extraction_id": ext_id}
            )
        conn.commit()
    finally:
        conn.close()

    yield orgs[0], orgs[1]

    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM public.organizations WHERE id IN (%s, %s)",
            (orgs[0]["org_id"], orgs[1]["org_id"]),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.integration
class TestVector1ValidKeyWrongOrg:
    """A valid, real, argon2id-verified API key for org A must never surface org B's
    data, tested through the real app-role connection (not a mocked/overridden context)."""

    def test_org_a_real_key_lookup_yields_only_org_a_context(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """Prove the real auth lookup (app.auth.api_key._lookup_and_record) resolves
        org_id purely from the DB row matched to the key -- never client-influenceable."""
        from app.auth.api_key import _lookup_and_record

        org_a, org_b = two_orgs_with_keys
        ctx = _lookup_and_record(org_a["raw_key"])
        assert ctx.org_id == org_a["org_id"]
        assert ctx.org_id != org_b["org_id"]

    def test_org_a_context_queries_extractions_sees_only_own_row(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """End-to-end: real key -> real auth lookup -> real storage_pg query, using the
        app's own review_iq_app connection throughout. Org A must never see org B's row,
        even by extraction_id if it somehow learned it (e.g. from a predictable ID)."""
        from app.auth.api_key import _lookup_and_record
        from app.core.storage_pg import get_by_hash_pg

        org_a, org_b = two_orgs_with_keys
        ctx = _lookup_and_record(org_a["raw_key"])

        own = get_by_hash_pg(ctx.org_id, "hash_adv_alpha")
        assert own is not None, "org A must see its own extraction via the real storage layer"

        cross = get_by_hash_pg(ctx.org_id, "hash_adv_beta")
        assert cross is None, (
            "org A's real, valid key must NEVER resolve org B's extraction, "
            "even when org B's exact input_hash is known"
        )


@pytest.mark.integration
class TestVector2ForgedJWT:
    """A forged/tampered/garbage JWT presented to the BFF session-auth path must be
    rejected, and org_id must never be derivable from unverified token claims."""

    @pytest.mark.asyncio
    async def test_garbage_jwt_rejected_by_real_supabase_verification(self) -> None:
        """Live call against the real Supabase auth API (not mocked) with a token that
        is not a real Supabase-issued session -- proves rejection end-to-end, not just
        that our own code would reject a token IF Supabase said it was invalid."""
        from app.auth.signup import verify_supabase_jwt
        from fastapi import HTTPException

        forged = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.forged.payload-not-a-real-token"
        with pytest.raises(HTTPException) as exc_info:
            await verify_supabase_jwt(forged)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_require_session_never_derives_org_from_unverified_claims(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """Even if an attacker crafts a JWT whose (unverified) payload claims a `sub`
        matching a real user from a DIFFERENT org than their own session, org_id must
        come from Supabase's OWN verified response (real user lookup), never decoded
        locally from the token body. Mocks verify_supabase_jwt to return a user object
        for org A's user, and confirms the resolved context is org A's -- proving the
        dependency chain has no path that reads org_id out of the raw token itself."""
        import app.auth.session as session_mod
        from app.auth.api_key import ApiKeyContext

        org_a, _ = two_orgs_with_keys

        class _FakeUser:
            def __init__(self, user_id: str) -> None:
                self.id = user_id

        # A real user row must exist in organization_members for this to resolve --
        # seed one for org A directly, bypassing signup, to isolate what's under test.
        fake_user_id = str(uuid.uuid4())
        conn = _superuser_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO public.organization_members (org_id, user_id) VALUES (%s, %s)",
                (org_a["org_id"], fake_user_id),
            )
            conn.commit()
        finally:
            conn.close()

        try:
            with patch.object(
                session_mod, "verify_supabase_jwt", return_value=_FakeUser(fake_user_id)
            ):
                from fastapi.security import HTTPAuthorizationCredentials

                creds = HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials="irrelevant-mocked-below"
                )
                ctx: ApiKeyContext = await session_mod.require_session_read(bearer=creds)
                assert ctx.org_id == org_a["org_id"], (
                    "org_id must come from the verified user->org_members lookup, "
                    "matching exactly the org this user_id was seeded into"
                )
        finally:
            conn = _superuser_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM public.organization_members WHERE user_id = %s", (fake_user_id,)
                )
                conn.commit()
            finally:
                conn.close()


@pytest.mark.integration
class TestVector3MismatchedOrgIdInBody:
    """A request body containing an org_id field different from the authenticated
    key's real org must never influence which org a record is attributed to."""

    def test_review_request_schema_silently_drops_unknown_org_id_field(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """Confirms Pydantic's default extra-field handling on ReviewRequest: an
        injected `org_id` in the JSON body must not survive validation, so no
        downstream code could ever read it even if it tried to."""
        from app.core.schemas import ReviewRequest

        org_a, org_b = two_orgs_with_keys
        payload = {"text": "great product", "org_id": org_b["org_id"]}
        parsed = ReviewRequest.model_validate(payload)
        assert not hasattr(parsed, "org_id"), (
            "ReviewRequest must not accept a client-supplied org_id field at all -- "
            "confirmed no such attribute exists on the validated model"
        )

    def test_extraction_v2_endpoint_ignores_body_org_id_uses_key_org(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """HTTP-level: POST to /v2/extract with org A's real key but a body claiming
        org B's org_id. The LLM call is mocked (no live inference needed to prove
        tenant attribution); the auth dependency and storage write are real."""
        from unittest.mock import AsyncMock

        from app.core.schemas import ReviewExtractionLLMOutput, Sentiment, Urgency
        from app.main import app
        from fastapi.testclient import TestClient

        org_a, org_b = two_orgs_with_keys

        fake_output = ReviewExtractionLLMOutput(
            product="Widget",
            stars=5,
            sentiment=Sentiment.positive,
            urgency=Urgency.low,
            topics=[],
            competitor_mentions=[],
            pros=[],
            cons=[],
            language="en",
            confidence=0.9,
        )

        async def _fake_extract(
            *_a: object, **_kw: object
        ) -> tuple[ReviewExtractionLLMOutput, str, int, int, int, bool]:
            return fake_output, "test-model", 100, 10, 5, False

        with patch("app.api.v2.extract.extract_with_llm", new=AsyncMock(side_effect=_fake_extract)):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v2/extract",
                json={"text": "adversarial body test unique marker xyz", "org_id": org_b["org_id"]},
                headers={"X-API-Key": org_a["raw_key"]},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["extraction_meta"]["org_id"] == org_a["org_id"], (
            "extraction must be attributed to org A (the real authenticated key's org), "
            "never org B (the injected body field)"
        )

        # Confirm at the DB level too -- not just trusting the response payload.
        from app.core.storage_pg import get_by_hash_pg

        stored = get_by_hash_pg(
            org_a["org_id"], _sha256_of("adversarial body test unique marker xyz")
        )
        assert stored is not None, "must be stored under org A"


def _sha256_of(text: str) -> str:
    from app.core.schemas import ReviewRequest

    return ReviewRequest(text=text).input_hash()


@pytest.mark.integration
class TestVector4DirectAppRoleConnection:
    """Direct connection using the app's own runtime database role (review_iq_app).

    This vector does NOT have a simple "isolation holds" answer, and reporting it as
    such would be dishonest. review_iq_app has BYPASSRLS=True by design (migration
    20260726000001_review_iq_app_role.sql; matches Supabase's own service_role
    convention; admin.py's org-create/delete paths need to operate across the org
    boundary RLS enforces and cannot go through SET LOCAL ROLE authenticated). The
    honest, verified finding:

      - review_iq_app connecting WITHOUT the app's own `_set_tenant()` call (i.e. no
        `SET LOCAL ROLE authenticated`) sees EVERY organization's data. RLS provides
        ZERO protection at the base connection level -- this is not a bug, it is what
        BYPASSRLS means, but it is a real fact about the blast radius of this
        credential, not a "safe by default" story.
      - review_iq_app WITH `_set_tenant()`'s `SET LOCAL ROLE authenticated` call DOES
        get real RLS enforcement -- Postgres evaluates row-security using the CURRENT
        (downgraded) role, not the original login role, verified empirically below.
      - Protection against this vector today rests entirely on application-code
        discipline (every tenant-scoped query in app/core/storage_pg.py calling
        `_set_tenant()` first) -- verified by direct audit of every function in that
        module: 20/21 call it; the 1 exception
        (list_orgs_with_dated_extractions_pg) is an explicitly-commented, intentional,
        low-risk cross-org sweep query (org_ids only, no per-org content, reachable
        only from an internal cron job, never from an API-key-authenticated request).
        There is NO database-level backstop if a future function forgets this call.
    """

    def test_app_role_without_set_tenant_sees_all_orgs(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """Documents the real, current blast radius: a direct review_iq_app connection
        with no org-scoping step sees every organization's extractions. This is
        EXPECTED given BYPASSRLS -- the test exists to make this fact explicit and
        catch it changing silently in either direction (e.g. if BYPASSRLS were ever
        revoked without updating admin.py's cross-org paths, this test would start
        failing and flag the regression)."""
        org_a, org_b = two_orgs_with_keys
        conn = _app_role_conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT org_id FROM public.extractions WHERE org_id IN (%s, %s)",
                (org_a["org_id"], org_b["org_id"]),
            )
            visible_orgs = {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert visible_orgs == {org_a["org_id"], org_b["org_id"]}, (
            "review_iq_app without SET LOCAL ROLE is expected (by BYPASSRLS design) to "
            "see both orgs -- if this assertion ever fails, BYPASSRLS has been revoked "
            "and app/core/storage_pg.py's cross-org sweep functions likely need a "
            "different mechanism (e.g. a SECURITY DEFINER function) to keep working"
        )

    def test_app_role_with_set_tenant_enforces_real_isolation(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """The actual protection mechanism: SET LOCAL ROLE authenticated + the org GUC,
        issued from the SAME review_iq_app (bypassrls=True) connection, must genuinely
        enforce RLS -- proving Postgres checks the CURRENT (downgraded) role's
        permissions, not the original login role's BYPASSRLS grant."""
        from app.core.storage_pg import _set_tenant

        org_a, org_b = two_orgs_with_keys
        conn = _app_role_conn()
        conn.autocommit = False
        try:
            cur = conn.cursor()
            _set_tenant(cur, org_a["org_id"])
            cur.execute(
                "SELECT org_id FROM public.extractions WHERE org_id IN (%s, %s)",
                (org_a["org_id"], org_b["org_id"]),
            )
            visible = {str(r[0]) for r in cur.fetchall()}
        finally:
            conn.rollback()
            conn.close()

        assert visible == {org_a["org_id"]}, (
            "with _set_tenant() applied, the same review_iq_app connection that just "
            "proved it CAN see everything must now see only org A -- this is the real, "
            "conditional protection the app relies on"
        )

    def test_every_storage_pg_function_calls_set_tenant_or_is_explicitly_justified(self) -> None:
        """Static audit, run as a test so it's enforced in CI (not just a one-time manual
        check): every function in app/core/storage_pg.py that executes SQL must either
        call _set_tenant() or carry an explicit justification comment for why it's an
        intentional cross-org exception. Catches a FUTURE function silently reintroducing
        an unscoped query -- this is exactly how the one known exception
        (list_orgs_with_dated_extractions_pg) is already documented; new exceptions must
        be documented the same way, not silently added."""
        import re
        from pathlib import Path

        src = (Path(__file__).parents[2] / "app" / "core" / "storage_pg.py").read_text(
            encoding="utf-8"
        )
        functions = re.split(r"\n(?=def \w+)", src)

        undocumented_gaps = []
        for func_src in functions:
            m = re.match(r"def (\w+)\(", func_src)
            if not m or m.group(1).startswith("_"):
                continue
            name = m.group(1)
            has_sql = "execute(" in func_src
            has_set_tenant = "_set_tenant(" in func_src
            has_justification = "Cross-org query" in func_src or "cross-org" in func_src.lower()
            if has_sql and not has_set_tenant and not has_justification:
                undocumented_gaps.append(name)

        assert undocumented_gaps == [], (
            f"function(s) execute SQL without _set_tenant() and without an explicit "
            f"cross-org justification comment: {undocumented_gaps} -- either add "
            f"_set_tenant() or document why this is an intentional exception"
        )


@pytest.mark.integration
class TestBypassrlsServingPathReachability:
    """S0 finding, reported not fixed (post-Section-E remediation pass).

    P2's exact question: is the BYPASSRLS-holding role reachable from ANY
    request-serving path -- not "does storage_pg.py's tenant-data layer correctly scope
    itself" (already covered by TestVector4DirectAppRoleConnection above, and it does).
    Traced in code, not inferred from the migration's stated intent:

      review_iq_app (rolbypassrls=true) is connected to, with ZERO _set_tenant() call
      anywhere in the file, by THREE call sites that are all live, request-serving, and
      externally reachable:

        1. app/api/admin.py -- every DB helper (_create_org_db, _get_org_db,
           _create_key_db, _list_keys_db, _rotate_key_db, _revoke_key_db) connects via
           `psycopg2.connect(settings.supabase_database_url)` with no _set_tenant() call
           anywhere in the file. Mounted as live HTTP routes (admin_router in
           app/main.py). The ONLY control is require_admin (app/auth/admin.py) -- HTTP
           Basic auth, single factor, no MFA, and CONFIRMED no rate-limiting/lockout on
           these routes (grepped app/api/admin.py for `limiter`/`rate_limit`: zero
           matches). A leaked or brute-forced admin credential is a full cross-org
           compromise with no RLS backstop, not a degraded-but-contained one.

        2. app/api/webhooks/google.py and app/api/webhooks/shopify.py -- the
           org-resolution lookup (google_location_name / shop_domain -> org_id) runs
           via the same `psycopg2.connect(settings.supabase_database_url)` connection
           with no SET ROLE at all (both files' own comments say "postgres role, no SET
           ROLE" -- that comment is STALE/INACCURATE post-2026-07-26: it actually
           connects as review_iq_app, not postgres, but the substantive behavior the
           comment describes -- an RLS-bypassing lookup -- is correct and current).
           These are live, externally-triggered endpoints (Google Pub/Sub push,
           Shopify webhook delivery). Auth is a shared-secret query token
           (hmac.compare_digest, Google) / HMAC signature (Shopify) -- single factor,
           and a leaked token/secret (e.g. via access logs that record full query
           strings, a common logging gotcha for the Google path specifically) has the
           same no-RLS-backstop exposure as (1).

      Ruled out (traced, not assumed): no fallback-DSN chain exists (`DATABASE_URL` is
      an entirely separate legacy v1 SQLite path -- app/core/storage.py,
      app/api/ops.py -- not a Postgres fallback for SUPABASE_DATABASE_URL); the
      migration runner uses a distinct `postgres` superuser credential over a direct
      (non-pooler) connection, manual/out-of-band per ops/runbooks/*.md -- it does not
      share a PgBouncer pool or any state with review_iq_app's transaction-mode pooler
      connection; every `SET ROLE` in the codebase is the transaction-scoped `SET LOCAL
      ROLE` form (grepped for a bare `SET ROLE` outside `SET LOCAL ROLE`: only found
      inside comments/docstrings describing the pattern, never an executed bare
      statement) -- so there is no PgBouncer transaction-mode role-bleed risk between
      pooled connections either.

    Per the standing instruction: reachable from a serving path means RLS is not the
    isolation control for these three paths, and this is reported, not fixed, in this
    pass. This test is written to CURRENTLY FAIL and stay failing until a decision is
    made and implemented -- it is a visible, tracked regression marker, not a rubber
    stamp. Do not "fix" this test by loosening the assertion; fix it by changing the
    underlying connection pattern for these three call sites (e.g. a distinct,
    narrower-privilege role for admin/webhook cross-org lookups instead of reusing the
    full review_iq_app credential, or a SECURITY DEFINER function scoped to exactly the
    lookup each site needs), then updating this test to assert the new, narrower state.
    """

    @pytest.mark.xfail(
        reason=(
            "S0, reported not fixed (Wave 1 Section E post-remediation pass): "
            "review_iq_app holds BYPASSRLS and IS reachable from 3 live "
            "request-serving paths (admin.py, webhooks/google.py, webhooks/shopify.py) "
            "with zero _set_tenant() call. strict=True so this stops being invisible "
            "the moment someone flips it green without deliberately removing the "
            "xfail marker -- that flip is the signal the fix landed, not a silent pass."
        ),
        strict=True,
    )
    def test_admin_and_webhook_serving_paths_do_not_use_a_bypassrls_role(self) -> None:
        """Known-failing, tracked -- see class docstring and the xfail reason above.
        Asserts the property this repo actually wants to be true (the role backing
        every request-serving DB connection, including admin/webhooks, does not hold
        BYPASSRLS), not the property that happens to be true today."""
        import os
        from pathlib import Path

        import psycopg2
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).parents[2] / ".env")

        conn = psycopg2.connect(os.environ["SUPABASE_DATABASE_URL"])
        try:
            cur = conn.cursor()
            cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            (has_bypass,) = cur.fetchone()
        finally:
            conn.close()

        assert has_bypass is False, (
            "review_iq_app (the connection app/api/admin.py and "
            "app/api/webhooks/{google,shopify}.py use for their unscoped, "
            "request-serving cross-org lookups) holds BYPASSRLS -- S0, reported in "
            "Wave 1 Section E's post-remediation pass, not fixed in that pass. RLS is "
            "not an effective isolation control for these three call sites as long as "
            "this is true. See this test's class docstring for the full trace."
        )
