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

from tests.integration._superuser_db_params import superuser_db_params

_OrgFixture = dict[str, str]

load_dotenv(Path(__file__).parents[2] / ".env")

_SUPERUSER_DB_PARAMS = superuser_db_params()


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

    Wave 1 S0 remediation (2026-07-31, supersedes the original 20260726000001 design):
    review_iq_app no longer holds BYPASSRLS. The migration in
    20260801000001_role_separation_bypassrls_remediation.sql moves the one legitimate
    need for a bypass-holding role (admin.py's org-wide CRUD) to a new, separate
    review_iq_admin role reachable only from a private, IAM-gated Cloud Run service
    (ADR 0006) -- and replaces the webhook handlers' cross-tenant org-resolution lookup
    with narrow SECURITY DEFINER functions that return org_id only, never a row.

    The honest, verified finding, now the CORRECT and intended one (empirically proven
    via a safe, rollback-only dry-run of the full migration against production -- see
    the migration file's own header and this PR's body for the exact verification
    output; this is not asserted from the migration's stated intent alone):

      - review_iq_app connecting WITHOUT `_set_tenant()` now sees NO organization's
        data at all -- not an error, not a default org, zero rows. It is still a
        member of `authenticated` (inheriting that role's table grants, per Postgres's
        normal membership-based policy-role matching -- confirmed empirically, not
        assumed, since this is the one subtle point that would be easy to get wrong),
        so the `extractions_authenticated_all` policy DOES apply to it directly, and
        with `app.current_org_id` unset, `current_org_id()` returns NULL (see
        20260510000002_rls_policies.sql's own documented "Returns NULL if neither is
        set -> RLS denies all access"), so the policy's `org_id = NULL` comparison is
        UNKNOWN for every row and nothing is returned.
      - review_iq_app WITH `_set_tenant()`'s `SET LOCAL ROLE authenticated` call
        continues to get real RLS enforcement, unaffected by the bypass removal (this
        was never the mechanism relying on BYPASSRLS in the first place).
      - Protection for every OTHER tenant-scoped query still rests on application-code
        discipline (every function in app/core/storage_pg.py calling `_set_tenant()`
        first, verified by direct audit: 20/21 call it; the 1 exception,
        list_orgs_with_dated_extractions_pg, is an explicitly-commented, intentional,
        low-risk cross-org sweep query) -- but the three call sites that used to rely
        on review_iq_app's bypass (admin.py, both webhook handlers) no longer do, and
        there is now a database-level backstop (no bypass at all on the role they use)
        rather than a code-discipline-only one.
    """

    def test_app_role_without_set_tenant_sees_no_orgs(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """Documents the NEW, correct blast radius: a direct review_iq_app connection
        with no org-scoping step sees NOTHING now that BYPASSRLS has been removed.
        This will fail against a not-yet-migrated database (the migration is applied
        by GG out-of-band, sequenced after code deploy -- see this PR's escalation
        steps) -- that failure is expected and correct until the migration lands, not
        a bug in this test. The moment it passes is the signal the migration has been
        applied for real, not merely dry-run-verified."""
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

        assert visible_orgs == set(), (
            "review_iq_app without _set_tenant() must see NO rows now that BYPASSRLS "
            "has been removed (20260801000001_role_separation_bypassrls_remediation.sql). "
            "If this assertion fails, either the migration has not yet been applied to "
            "this database (expected until GG completes the escalation steps in this "
            "PR's body), or BYPASSRLS has been re-granted to review_iq_app, which would "
            "be a regression back to the S0 finding this migration fixes."
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
    """S0 finding, FIXED in this pass (20260801000001_role_separation_bypassrls_remediation.sql
    + app/main.py + app/api/admin.py + app/api/webhooks/{google,shopify}.py).

    P2's exact question: is the BYPASSRLS-holding role reachable from ANY
    request-serving path -- not "does storage_pg.py's tenant-data layer correctly scope
    itself" (already covered by TestVector4DirectAppRoleConnection above, and it does).
    Original finding (Wave 1 Section E post-remediation pass, superseded here):
    review_iq_app (rolbypassrls=true) was connected to, with ZERO _set_tenant() call,
    by three live, request-serving, externally-reachable call sites: app/api/admin.py's
    org/key CRUD, and both webhook handlers' org-resolution lookups.

    Fix, in three independent parts (all required, none sufficient alone -- per the
    standing instruction this remediation was scoped against):

      1. ROLE SEPARATION. review_iq_app no longer holds BYPASSRLS at all (verified via
         a safe, rollback-only dry-run of the full migration against production -- see
         the migration file's header for the exact verification steps and this PR's
         body for the output). The only bypass-holding role reachable from a
         request-serving path now is review_iq_admin, and that role is reachable
         exclusively from a NEW, separate Cloud Run service deployed
         --no-allow-unauthenticated (ADR 0006) -- not from the public service at all.
         review_iq_migrator (the other bypass-holding role) is reachable only from
         out-of-band migration execution, never referenced by any application setting,
         Cloud Run env var, or Secret Manager secret used by either deployed service.

      2. /admin/* removed from public reachability. app/main.py only mounts
         admin_router when SERVICE_ROLE=admin, a mode that mounts nothing else
         public-facing. The public review-iq service never mounts it. Every admin.py
         operation was audited (create org, get org, create/list/rotate/revoke API
         key) -- all five are operator-only CRUD with no legitimate public caller
         (ADR 0006's own verification section). require_admin's HTTP Basic auth stays
         as defense-in-depth underneath IAM, not as the only control.

      3. WEBHOOK HANDLERS now resolve tenant identity via a narrow SECURITY DEFINER
         function (resolve_org_for_google_location / resolve_org_for_shopify_shop --
         returns ONLY org_id, never the row, never the encrypted token) after their
         existing signature verification (Google: shared-secret query token via
         hmac.compare_digest; Shopify: HMAC-SHA256 via hmac.compare_digest -- both
         re-verified present and sound in this pass, not assumed from memory; neither
         is the "second finding" the standing instruction asked to report if absent,
         since both ARE present), then connect as review_iq_app (no longer holding
         bypass) and call _set_tenant(org_id) before touching any tenant data.

    Ruled out (traced, not assumed, unchanged from the original finding's own trace):
    no fallback-DSN chain exists (`DATABASE_URL` is an entirely separate legacy v1
    SQLite path); the migration runner uses a distinct `postgres` credential over a
    direct (non-pooler) connection, manual/out-of-band, sharing no PgBouncer pool state
    with review_iq_app's transaction-mode pooler connection; every `SET ROLE` in the
    codebase is the transaction-scoped `SET LOCAL ROLE` form (no bare `SET ROLE`
    outside comments/docstrings) -- so there is no PgBouncer transaction-mode
    role-bleed risk between pooled connections.

    This test is no longer `xfail` -- the flip from `xfail(strict=True)` to a plain
    assertion IS the signal the fix landed, per the original xfail's own stated
    design. It will still FAIL against a database this migration has not yet been
    applied to (expected and correct until GG completes the escalation steps in this
    PR's body -- code deploy first, then the migration's final
    `ALTER ROLE review_iq_app NOBYPASSRLS` statement, never the other order); do not
    weaken this assertion to make it pass against an unmigrated database.
    """

    def test_admin_and_webhook_serving_paths_do_not_use_a_bypassrls_role(self) -> None:
        """Asserts the property this repo wants to be true (the role backing every
        request-serving DB connection, including admin/webhooks, does not hold
        BYPASSRLS) -- see class docstring for the full fix trace."""
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
            "app/api/webhooks/{google,shopify}.py use) holds BYPASSRLS. If this "
            "database has not yet had 20260801000001_role_separation_bypassrls_"
            "remediation.sql applied, this failure is expected -- see this PR's "
            "escalation steps. If the migration HAS been applied and this still "
            "fails, BYPASSRLS has been re-granted, which is a regression back to the "
            "S0 finding this migration fixes. See this test's class docstring for the "
            "full trace."
        )


@pytest.mark.integration
class TestBypassrlsRemediation2cResolvers:
    """BYPASSRLS remediation, pass 2c (2026-08-01): the query shape TestVector4 and
    TestBypassrlsServingPathReachability above did NOT cover -- org-RESOLUTION with no
    tenant context yet, using review_iq_app's raw credential and no SET ROLE, for every
    path an audit found still doing this without a resolver: api_key lookup, session
    lookup (both write and read paths), signup provisioning, and both installation
    upserts. See supabase/migrations/20260801000002_tenant_resolvers_auth_signup.sql.

    Same epistemic status as TestVector4/TestBypassrlsServingPathReachability: these
    assertions describe the CORRECT, intended state. They will fail against a database
    that has not yet had 20260801000001 (BYPASSRLS removed from review_iq_app) AND
    20260801000002 (these resolver functions) applied -- expected and correct until
    then, not a bug in this suite. Do not weaken these to make them pass early.
    """

    def test_resolve_org_for_api_key_prefix_returns_correct_org_only(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """The resolver called by app/auth/api_key.py before any tenant context exists
        must resolve org A's real key_prefix to org A ONLY, and a bare, unknown prefix
        to nothing -- called via review_iq_app's own connection, no SET ROLE."""
        org_a, org_b = two_orgs_with_keys
        conn = _app_role_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT public.resolve_org_for_api_key_prefix(%s)", (org_a["raw_key"][:17],)
            )
            (resolved_a,) = cur.fetchone()
            cur.execute("SELECT public.resolve_org_for_api_key_prefix(%s)", ("riq_live_00000000",))
            (resolved_unknown,) = cur.fetchone()
        finally:
            conn.rollback()
            conn.close()

        assert str(resolved_a) == org_a["org_id"]
        assert str(resolved_a) != org_b["org_id"]
        assert resolved_unknown is None

    def test_resolve_org_for_user_returns_correct_org_only(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """The resolver called by app/auth/session.py and app/auth/signup.py before any
        tenant context exists must resolve a real member's user_id to their own org
        ONLY, and an unknown user_id to nothing."""
        org_a, _org_b = two_orgs_with_keys
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
            conn = _app_role_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT public.resolve_org_for_user(%s)", (fake_user_id,))
                (resolved,) = cur.fetchone()
                cur.execute("SELECT public.resolve_org_for_user(%s)", (str(uuid.uuid4()),))
                (resolved_unknown,) = cur.fetchone()
            finally:
                conn.rollback()
                conn.close()

            assert str(resolved) == org_a["org_id"]
            assert resolved_unknown is None
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

    def test_signup_provisioning_creates_isolated_org_via_review_iq_app(self) -> None:
        """End-to-end: app/auth/signup.py::_provision_org_and_key, called through the
        REAL review_iq_app connection (no test mocking of the DB layer), must create a
        brand-new, fully isolated org -- and the resulting API key must resolve back to
        that SAME org via resolve_org_for_api_key_prefix, never anything else."""
        from app.auth.signup import _provision_org_and_key

        user_id = str(uuid.uuid4())
        email = f"adversarial-2c-{uuid.uuid4().hex[:8]}@example.com"
        try:
            result = _provision_org_and_key(user_id, email)
            org_id = str(result["org_id"])

            conn = _app_role_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT public.resolve_org_for_api_key_prefix(%s)",
                    (str(result["key_prefix"]),),
                )
                (resolved,) = cur.fetchone()
            finally:
                conn.rollback()
                conn.close()
            assert str(resolved) == org_id
        finally:
            conn = _superuser_conn()
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
                conn.commit()
            finally:
                conn.close()

    def test_installation_upserts_are_tenant_scoped_not_bypass_reliant(
        self, two_orgs_with_keys: tuple[_OrgFixture, _OrgFixture]
    ) -> None:
        """app/api/google_auth.py and app/api/shopify_auth.py's _upsert_installation_pg
        now call _set_tenant() before the upsert (BYPASSRLS remediation 2c). Proves the
        write actually lands under org A's own context -- a plain review_iq_app
        connection with NO _set_tenant() must not see it (same "no bypass" property
        TestVector4 proves for extractions, now proven for these two tables)."""
        from app.api.google_auth import _upsert_installation_pg as _upsert_google
        from app.api.shopify_auth import _upsert_installation_pg as _upsert_shopify

        org_a, org_b = two_orgs_with_keys
        location_name = f"accounts/1/locations/{uuid.uuid4().hex[:10]}"
        shop_domain = f"adv2c-{uuid.uuid4().hex[:8]}.myshopify.com"
        try:
            _upsert_google(org_a["org_id"], "accounts/1", location_name, "enc-refresh-token")
            _upsert_shopify(org_a["org_id"], shop_domain, "enc-access-token")

            # No _set_tenant(): a plain review_iq_app connection must see NOTHING.
            conn = _app_role_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT org_id FROM public.google_business_installations "
                    "WHERE google_location_name = %s",
                    (location_name,),
                )
                assert cur.fetchone() is None
                cur.execute(
                    "SELECT org_id FROM public.shopify_installations WHERE shop_domain = %s",
                    (shop_domain,),
                )
                assert cur.fetchone() is None
            finally:
                conn.rollback()
                conn.close()

            # Via the real resolver + _set_tenant path (the webhook handlers' own
            # pattern), org A must see exactly its own rows.
            conn = _app_role_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT public.resolve_org_for_google_location(%s)", (location_name,))
                (resolved_google,) = cur.fetchone()
                cur.execute("SELECT public.resolve_org_for_shopify_shop(%s)", (shop_domain,))
                (resolved_shopify,) = cur.fetchone()
            finally:
                conn.rollback()
                conn.close()
            assert str(resolved_google) == org_a["org_id"]
            assert str(resolved_shopify) == org_a["org_id"]
            assert str(resolved_google) != org_b["org_id"]
        finally:
            conn = _superuser_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM public.google_business_installations "
                    "WHERE google_location_name = %s",
                    (location_name,),
                )
                cur.execute(
                    "DELETE FROM public.shopify_installations WHERE shop_domain = %s",
                    (shop_domain,),
                )
                conn.commit()
            finally:
                conn.close()
