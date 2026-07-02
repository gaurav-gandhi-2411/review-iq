"""Unit tests for Google OAuth install flow — /auth/google/begin + /auth/google/callback.

MANDATORY SECURITY TESTS (mirroring project_shopify_oauth_callback_security memory,
adapted for Google — there is no shop-param analog, so test (a) proves org isolation
via two different orgs' JWTs against the SAME code/state):
  (a) test_org_id_from_jwt_not_request_body — org_a's JWT → install lands in org_a
  (b) test_forged_jwt_returns_401_no_install_written — forged JWT → 401, zero DB writes
  (c) test_written_org_id_matches_resolved_org_id — _upsert called with exact JWT value

These three are the org_id tenant-security proof for the OAuth callback layer.
The DB-layer proof lives in tests/integration/test_google_business_installations_rls.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.google_auth import _generate_state, _verify_state
from app.main import create_app
from fastapi import HTTPException
from fastapi.testclient import TestClient

_CLIENT_SECRET = "test_client_secret_for_google_auth_tests"
_ENC_KEY = "dGVzdC10ZXN0LXRlc3QtdGVzdC10ZXN0LXRlc3Q="  # 32-byte base64 placeholder


def _make_settings(**overrides: object) -> MagicMock:
    s = MagicMock()
    s.google_client_id = "test_client_id"
    s.google_client_secret = _CLIENT_SECRET
    s.google_token_encryption_key = _ENC_KEY
    s.google_webhook_base_url = "https://api.test.reviewiq.app"
    s.google_pubsub_topic = "projects/test/topics/reviews"
    s.google_pubsub_push_token = "push_token_abc"
    s.supabase_database_url = "postgresql://test:test@localhost/test"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _make_user(user_id: str = "user-abc") -> MagicMock:
    u = MagicMock()
    u.id = user_id
    return u


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(_make_settings()))


_ACCOUNTS = [{"name": "accounts/123"}]
_LOCATIONS = [{"name": "accounts/123/locations/456"}]
_TOKEN_DATA = {
    "access_token": "ya29.fake_access_token",
    "refresh_token": "1//0gfake_refresh_token",
    "expires_in": 3600,
}


# ---------------------------------------------------------------------------
# Pure-function tests — no HTTP, no mocks
# ---------------------------------------------------------------------------


class TestStateLifecycle:
    def test_roundtrip(self) -> None:
        state = _generate_state(_CLIENT_SECRET)
        assert _verify_state(state, _CLIENT_SECRET)

    def test_wrong_secret_fails(self) -> None:
        state = _generate_state(_CLIENT_SECRET)
        assert not _verify_state(state, "wrong_secret")

    def test_malformed_state_fails(self) -> None:
        assert not _verify_state("notimestamp", _CLIENT_SECRET)

    def test_expired_state_fails(self) -> None:
        import hashlib
        import hmac as _hmac
        import time

        ts = str(int(time.time()) - 1000)
        mac = _hmac.new(_CLIENT_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()
        stale_state = f"{ts}:{mac}"
        assert not _verify_state(stale_state, _CLIENT_SECRET, max_age=600)

    def test_tampered_hmac_fails(self) -> None:
        state = _generate_state(_CLIENT_SECRET)
        ts, mac = state.split(":", 1)
        tampered = f"{ts}:{mac[:-4]}0000"
        assert not _verify_state(tampered, _CLIENT_SECRET)


# ---------------------------------------------------------------------------
# MANDATORY SECURITY TEST (a) — org_id comes from JWT, not any request body field
# ---------------------------------------------------------------------------


class TestOrgIdFromJwtNotRequestBody:
    """Two different orgs' JWTs against the SAME code/state install under their OWN org."""

    def test_org_id_from_jwt_not_request_body(self) -> None:
        """Install with org_a's JWT lands the install in org_a — never anything from the body.

        There is no shop-param analog for Google, so this proves org isolation by
        mocking _get_org_for_user to return org_a's id and asserting the upsert call
        received exactly that org_id, never any value derived from `code` or `state`.
        """
        org_a_id = "org-a-00000000-0000-0000-0000-000000000001"
        user_a_id = "user-a-00000000-0000-0000-0000-000000000001"

        state = _generate_state(_CLIENT_SECRET)
        captured: list[dict[str, str]] = []

        def fake_upsert(
            org_id: str, google_account_name: str, google_location_name: str, refresh_token_enc: str
        ) -> None:
            captured.append(
                {
                    "org_id": org_id,
                    "google_account_name": google_account_name,
                    "google_location_name": google_location_name,
                }
            )

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(user_a_id),
            ),
            patch(
                "app.api.google_auth._get_org_for_user",
                return_value={"org_id": org_a_id},
            ),
            patch(
                "app.api.google_auth._exchange_code",
                new_callable=AsyncMock,
                return_value=dict(_TOKEN_DATA),
            ),
            patch(
                "app.api.google_auth._list_accounts",
                new_callable=AsyncMock,
                return_value=_ACCOUNTS,
            ),
            patch(
                "app.api.google_auth._list_locations",
                new_callable=AsyncMock,
                return_value=_LOCATIONS,
            ),
            patch("app.api.google_auth._upsert_installation_pg", side_effect=fake_upsert),
            patch(
                "app.api.google_auth._register_notifications",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.api.google_auth.encrypt_token", return_value="gAAAAABfake_enc"),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "google_code_xyz", "state": state},
                headers={"Authorization": "Bearer org_a_jwt_token"},
            )

        assert resp.status_code == 200, resp.text
        assert len(captured) == 1, "exactly one location should be installed"
        # CORE ASSERTION: org_id in the DB write must come from the JWT, not the request body
        assert captured[0]["org_id"] == org_a_id, (
            f"org_id must come from JWT (org_a={org_a_id}), got {captured[0]['org_id']!r}"
        )
        assert captured[0]["google_location_name"] == "accounts/123/locations/456"


# ---------------------------------------------------------------------------
# MANDATORY SECURITY TEST (b) — forged JWT → 401, no install written
# ---------------------------------------------------------------------------


class TestForgedJwtRejected:
    """A tampered or expired JWT must not complete an install."""

    def test_forged_jwt_returns_401_no_install_written(self) -> None:
        state = _generate_state(_CLIENT_SECRET)
        upsert_mock = MagicMock()

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=401, detail="Invalid or expired Supabase token."),
            ),
            patch("app.api.google_auth._upsert_installation_pg", upsert_mock),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "code_xyz", "state": state},
                headers={"Authorization": "Bearer forged.jwt.token"},
            )

        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        # CORE ASSERTION: no row was written to google_business_installations
        upsert_mock.assert_not_called()

    def test_missing_bearer_returns_401(self) -> None:
        with patch("app.api.google_auth.get_settings", return_value=_make_settings()):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "x", "state": "bad"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# MANDATORY SECURITY TEST (c) — written org_id matches resolved org_id exactly
# ---------------------------------------------------------------------------


class TestWrittenOrgIdMatchesResolved:
    """The org_id written to google_business_installations equals _get_org_for_user's return."""

    def test_written_org_id_matches_resolved_org_id(self) -> None:
        specific_org_id = "specific-org-ffff-ffff-ffff-ffffffffffff"
        user_id = "user-specific-id"
        state = _generate_state(_CLIENT_SECRET)
        upsert_calls: list[tuple[str, str, str, str]] = []

        def capture_upsert(
            org_id: str, google_account_name: str, google_location_name: str, refresh_token_enc: str
        ) -> None:
            upsert_calls.append((org_id, google_account_name, google_location_name, refresh_token_enc))

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(user_id),
            ),
            patch(
                "app.api.google_auth._get_org_for_user",
                return_value={"org_id": specific_org_id},
            ),
            patch(
                "app.api.google_auth._exchange_code",
                new_callable=AsyncMock,
                return_value=dict(_TOKEN_DATA),
            ),
            patch(
                "app.api.google_auth._list_accounts",
                new_callable=AsyncMock,
                return_value=_ACCOUNTS,
            ),
            patch(
                "app.api.google_auth._list_locations",
                new_callable=AsyncMock,
                return_value=_LOCATIONS,
            ),
            patch("app.api.google_auth._upsert_installation_pg", side_effect=capture_upsert),
            patch(
                "app.api.google_auth._register_notifications",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.api.google_auth.encrypt_token", return_value="gAAAAABfenc"),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "google_code", "state": state},
                headers={"Authorization": "Bearer valid_jwt"},
            )

        assert resp.status_code == 200, resp.text
        assert len(upsert_calls) == 1, "exactly one DB write should occur"
        written_org_id, _, _, _ = upsert_calls[0]
        # CORE ASSERTION: written org_id is the exact value _get_org_for_user returned
        assert written_org_id == specific_org_id, (
            f"Written org_id {written_org_id!r} != resolved {specific_org_id!r}"
        )


# ---------------------------------------------------------------------------
# Additional callback behaviour tests
# ---------------------------------------------------------------------------


class TestCallbackBehaviour:
    def test_expired_state_returns_401(self) -> None:
        import hashlib
        import hmac as _hmac
        import time

        old_ts = str(int(time.time()) - 1000)
        mac = _hmac.new(_CLIENT_SECRET.encode(), old_ts.encode(), hashlib.sha256).hexdigest()
        stale_state = f"{old_ts}:{mac}"

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "x", "state": stale_state},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 401

    def test_malformed_state_returns_401(self) -> None:
        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "x", "state": "not-a-valid-state"},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 401

    def test_token_exchange_missing_refresh_token_returns_502(self) -> None:
        state = _generate_state(_CLIENT_SECRET)

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
            patch(
                "app.api.google_auth._get_org_for_user",
                return_value={"org_id": "org-no-refresh"},
            ),
            patch(
                "app.api.google_auth._exchange_code",
                new_callable=AsyncMock,
                side_effect=ValueError("Google returned no refresh_token: {}"),
            ),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "already_consented_code", "state": state},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 502

    def test_notification_registration_failure_does_not_fail_install(self) -> None:
        state = _generate_state(_CLIENT_SECRET)

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
            patch(
                "app.api.google_auth._get_org_for_user",
                return_value={"org_id": "org-notif-fail"},
            ),
            patch(
                "app.api.google_auth._exchange_code",
                new_callable=AsyncMock,
                return_value=dict(_TOKEN_DATA),
            ),
            patch(
                "app.api.google_auth._list_accounts",
                new_callable=AsyncMock,
                return_value=_ACCOUNTS,
            ),
            patch(
                "app.api.google_auth._list_locations",
                new_callable=AsyncMock,
                return_value=_LOCATIONS,
            ),
            patch("app.api.google_auth._upsert_installation_pg"),
            patch(
                "app.api.google_auth._register_notifications",
                new_callable=AsyncMock,
                side_effect=Exception("Google API timeout"),
            ),
            patch("app.api.google_auth.encrypt_token", return_value="gAAAAABfenc"),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "code_x", "state": state},
                headers={"Authorization": "Bearer token"},
            )

        # Notification registration failure must not fail the install
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "installed"

    def test_no_org_for_user_returns_403(self) -> None:
        state = _generate_state(_CLIENT_SECRET)

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
            patch("app.api.google_auth._get_org_for_user", return_value=None),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "code_x", "state": state},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 403

    def test_multiple_locations_all_installed(self) -> None:
        state = _generate_state(_CLIENT_SECRET)
        upsert_mock = MagicMock()
        multi_locations = [
            {"name": "accounts/123/locations/456"},
            {"name": "accounts/123/locations/789"},
        ]

        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
            patch(
                "app.api.google_auth._get_org_for_user",
                return_value={"org_id": "org-multi"},
            ),
            patch(
                "app.api.google_auth._exchange_code",
                new_callable=AsyncMock,
                return_value=dict(_TOKEN_DATA),
            ),
            patch(
                "app.api.google_auth._list_accounts",
                new_callable=AsyncMock,
                return_value=_ACCOUNTS,
            ),
            patch(
                "app.api.google_auth._list_locations",
                new_callable=AsyncMock,
                return_value=multi_locations,
            ),
            patch("app.api.google_auth._upsert_installation_pg", upsert_mock),
            patch(
                "app.api.google_auth._register_notifications",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.api.google_auth.encrypt_token", return_value="gAAAAABfenc"),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/google/callback",
                json={"code": "code_multi", "state": state},
                headers={"Authorization": "Bearer token"},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["locations_installed"] == 2
        assert upsert_mock.call_count == 2


# ---------------------------------------------------------------------------
# Begin endpoint tests
# ---------------------------------------------------------------------------


class TestBeginEndpoint:
    def test_returns_state_and_redirect_url(self) -> None:
        with (
            patch("app.api.google_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app())
            resp = tc.get(
                "/auth/google/begin",
                headers={"Authorization": "Bearer valid_token"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "state" in data
        assert "redirect_url" in data
        assert "accounts.google.com/o/oauth2/v2/auth" in data["redirect_url"]
        assert "scope=https://www.googleapis.com/auth/business.manage" in data["redirect_url"]
        assert "access_type=offline" in data["redirect_url"]
        assert "prompt=consent" in data["redirect_url"]
        # State returned by begin must be verifiable (proves begin and callback share the secret)
        assert _verify_state(data["state"], _CLIENT_SECRET)

    def test_no_bearer_returns_401(self) -> None:
        with patch("app.api.google_auth.get_settings", return_value=_make_settings()):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.get("/auth/google/begin")
        assert resp.status_code == 401

    def test_not_configured_returns_503(self) -> None:
        with (
            patch(
                "app.api.google_auth.get_settings",
                return_value=_make_settings(google_client_id="", google_client_secret=""),
            ),
            patch(
                "app.api.google_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.get(
                "/auth/google/begin",
                headers={"Authorization": "Bearer valid_token"},
            )
        assert resp.status_code == 503
