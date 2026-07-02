"""Unit tests for Shopify OAuth install flow — /auth/shopify/begin + /auth/shopify/callback.

MANDATORY SECURITY TESTS (from project_shopify_oauth_callback_security memory):
  (a) test_org_id_from_jwt_not_shop_param — different org's JWT lands install in THAT org
  (b) test_forged_jwt_returns_401_no_install_written — forged JWT → 401, zero DB writes
  (c) test_written_org_id_matches_resolved_org_id — _upsert called with exact value from JWT

These three are the org_id tenant-security proof for the OAuth callback layer.
The DB-layer proof lives in tests/integration/test_shopify_installations_rls.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.shopify_auth import (
    _generate_state,
    _validate_shop,
    _verify_shopify_callback_hmac,
    _verify_state,
)
from app.main import create_app

_CLIENT_SECRET = "test_client_secret_for_auth_tests"
_ENC_KEY = "dGVzdC10ZXN0LXRlc3QtdGVzdC10ZXN0LXRlc3Q="  # 32-byte base64 placeholder


def _make_settings(**overrides: object) -> MagicMock:
    s = MagicMock()
    s.shopify_client_id = "test_client_id"
    s.shopify_client_secret = _CLIENT_SECRET
    s.shopify_api_version = "2024-10"
    s.shopify_token_encryption_key = _ENC_KEY
    s.shopify_webhook_base_url = "https://api.test.reviewiq.app"
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


# ---------------------------------------------------------------------------
# Pure-function tests — no HTTP, no mocks
# ---------------------------------------------------------------------------


class TestValidateShop:
    def test_valid_domain_normalises_to_lower(self) -> None:
        assert _validate_shop("Store.MyShopify.COM") == "store.myshopify.com"

    def test_rejects_non_myshopify_domain(self) -> None:
        with pytest.raises(ValueError):
            _validate_shop("store.shopify.com")

    def test_rejects_bare_shop_name(self) -> None:
        with pytest.raises(ValueError):
            _validate_shop("mystore")


class TestStateLifecycle:
    def test_roundtrip(self) -> None:
        state = _generate_state("store.myshopify.com", _CLIENT_SECRET)
        assert _verify_state(state, "store.myshopify.com", _CLIENT_SECRET)

    def test_wrong_shop_fails(self) -> None:
        state = _generate_state("store-a.myshopify.com", _CLIENT_SECRET)
        assert not _verify_state(state, "store-b.myshopify.com", _CLIENT_SECRET)

    def test_wrong_secret_fails(self) -> None:
        state = _generate_state("store.myshopify.com", _CLIENT_SECRET)
        assert not _verify_state(state, "store.myshopify.com", "wrong_secret")

    def test_malformed_state_fails(self) -> None:
        assert not _verify_state("notimestamp", "store.myshopify.com", _CLIENT_SECRET)

    def test_expired_state_fails(self) -> None:
        # Craft a state with timestamp 1000 seconds ago
        import hashlib
        import hmac as _hmac

        ts = str(int(__import__("time").time()) - 1000)
        msg = f"store.myshopify.com:{ts}"
        mac = _hmac.new(_CLIENT_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        stale_state = f"{ts}:{mac}"
        assert not _verify_state(stale_state, "store.myshopify.com", _CLIENT_SECRET, max_age=600)


class TestShopifyCallbackHmac:
    def test_valid_hmac_accepted(self) -> None:
        import hashlib
        import hmac as _hmac

        secret = "shop_secret"
        params = {"code": "abc", "shop": "s.myshopify.com", "state": "xyz", "timestamp": "123"}
        msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["hmac"] = _hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        assert _verify_shopify_callback_hmac(params, secret)

    def test_tampered_code_rejected(self) -> None:
        import hashlib
        import hmac as _hmac

        secret = "shop_secret"
        params = {"code": "abc", "shop": "s.myshopify.com", "state": "xyz", "timestamp": "123"}
        msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        params["hmac"] = _hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        params["code"] = "tampered"
        assert not _verify_shopify_callback_hmac(params, secret)

    def test_missing_hmac_returns_false(self) -> None:
        assert not _verify_shopify_callback_hmac({"code": "x", "shop": "s.myshopify.com"}, "s")


# ---------------------------------------------------------------------------
# MANDATORY SECURITY TEST (a) — org_id comes from JWT, not shop param
# ---------------------------------------------------------------------------


class TestOrgIdFromJwtNotShopParam:
    """A seller authenticating as org_a cannot install under org_b by passing org_b's shop."""

    def test_org_id_from_jwt_not_shop_param(self) -> None:
        """Install with org_a's JWT but org_b's shop → install lands in org_a, not org_b.

        The shop param is used only for token exchange and webhook registration.
        org_id is resolved exclusively from the verified JWT.
        """
        shop = "org-b-shop.myshopify.com"
        org_a_id = "org-a-00000000-0000-0000-0000-000000000001"
        user_a_id = "user-a-00000000-0000-0000-0000-000000000001"

        state = _generate_state(shop, _CLIENT_SECRET)
        captured: dict[str, str] = {}

        def fake_upsert(org_id: str, shop_domain: str, access_token_enc: str) -> None:
            captured["org_id"] = org_id
            captured["shop_domain"] = shop_domain

        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(user_a_id),
            ),
            patch(
                "app.api.shopify_auth._get_org_for_user",
                return_value={"org_id": org_a_id},
            ),
            patch(
                "app.api.shopify_auth._exchange_code",
                new_callable=AsyncMock,
                return_value="shpat_fake_token",
            ),
            patch("app.api.shopify_auth._upsert_installation_pg", side_effect=fake_upsert),
            patch(
                "app.api.shopify_auth._register_webhook",
                new_callable=AsyncMock,
                return_value="webhook_123",
            ),
            patch(
                "app.api.shopify_auth.encrypt_token",
                return_value="gAAAAABfake_enc",
            ),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "shopify_code_xyz", "shop": shop, "state": state},
                headers={"Authorization": "Bearer org_a_jwt_token"},
            )

        assert resp.status_code == 200, resp.text
        # CORE ASSERTION: org_id in the DB write must come from the JWT, not the shop param
        assert captured["org_id"] == org_a_id, (
            f"org_id must come from JWT (org_a={org_a_id}), got {captured['org_id']!r}"
        )
        assert captured["shop_domain"] == shop  # shop param used only for domain routing


# ---------------------------------------------------------------------------
# MANDATORY SECURITY TEST (b) — forged JWT → 401, no install written
# ---------------------------------------------------------------------------


class TestForgedJwtRejected:
    """A tampered or expired JWT must not complete an install."""

    def test_forged_jwt_returns_401_no_install_written(self) -> None:
        shop = "legit-store.myshopify.com"
        state = _generate_state(shop, _CLIENT_SECRET)
        upsert_mock = MagicMock()

        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=401, detail="Invalid or expired Supabase token."),
            ),
            patch("app.api.shopify_auth._upsert_installation_pg", upsert_mock),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "code_xyz", "shop": shop, "state": state},
                headers={"Authorization": "Bearer forged.jwt.token"},
            )

        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        # CORE ASSERTION: no row was written to shopify_installations
        upsert_mock.assert_not_called()

    def test_missing_bearer_returns_401(self) -> None:
        with patch("app.api.shopify_auth.get_settings", return_value=_make_settings()):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "x", "shop": "s.myshopify.com", "state": "bad"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# MANDATORY SECURITY TEST (c) — written org_id matches resolved org_id exactly
# ---------------------------------------------------------------------------


class TestWrittenOrgIdMatchesResolved:
    """The org_id written to shopify_installations equals _get_org_for_user's return value."""

    def test_written_org_id_matches_resolved_org_id(self) -> None:
        shop = "correct-org-store.myshopify.com"
        specific_org_id = "specific-org-ffff-ffff-ffff-ffffffffffff"
        user_id = "user-specific-id"
        state = _generate_state(shop, _CLIENT_SECRET)
        upsert_calls: list[tuple[str, str, str]] = []

        def capture_upsert(org_id: str, shop_domain: str, access_token_enc: str) -> None:
            upsert_calls.append((org_id, shop_domain, access_token_enc))

        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(user_id),
            ),
            patch(
                "app.api.shopify_auth._get_org_for_user",
                return_value={"org_id": specific_org_id},
            ),
            patch(
                "app.api.shopify_auth._exchange_code",
                new_callable=AsyncMock,
                return_value="shpat_real_token",
            ),
            patch("app.api.shopify_auth._upsert_installation_pg", side_effect=capture_upsert),
            patch(
                "app.api.shopify_auth._register_webhook",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.api.shopify_auth.encrypt_token", return_value="gAAAAABfenc"),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "shopify_code", "shop": shop, "state": state},
                headers={"Authorization": "Bearer valid_jwt"},
            )

        assert resp.status_code == 200, resp.text
        assert len(upsert_calls) == 1, "exactly one DB write should occur"
        written_org_id, _, _ = upsert_calls[0]
        # CORE ASSERTION: written org_id is the exact value _get_org_for_user returned
        assert written_org_id == specific_org_id, (
            f"Written org_id {written_org_id!r} != resolved {specific_org_id!r}"
        )


# ---------------------------------------------------------------------------
# Additional callback behaviour tests
# ---------------------------------------------------------------------------


class TestCallbackBehaviour:
    def test_invalid_shop_domain_returns_400(self) -> None:
        shop = "not-a-shopify-store.com"
        state = "any_state"
        with patch("app.api.shopify_auth.get_settings", return_value=_make_settings()):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "x", "shop": shop, "state": state},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 400

    def test_expired_state_returns_401(self) -> None:
        import hashlib
        import hmac as _hmac

        shop = "expired-state-store.myshopify.com"
        old_ts = str(int(__import__("time").time()) - 1000)
        msg = f"{shop}:{old_ts}"
        mac = _hmac.new(_CLIENT_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        stale_state = f"{old_ts}:{mac}"

        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "x", "shop": shop, "state": stale_state},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 401

    def test_webhook_registration_failure_does_not_fail_install(self) -> None:
        shop = "webhook-fail-store.myshopify.com"
        state = _generate_state(shop, _CLIENT_SECRET)

        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
            patch(
                "app.api.shopify_auth._get_org_for_user",
                return_value={"org_id": "org-webhook-fail"},
            ),
            patch(
                "app.api.shopify_auth._exchange_code",
                new_callable=AsyncMock,
                return_value="shpat_token",
            ),
            patch("app.api.shopify_auth._upsert_installation_pg"),
            patch(
                "app.api.shopify_auth._register_webhook",
                new_callable=AsyncMock,
                side_effect=Exception("Shopify API timeout"),
            ),
            patch("app.api.shopify_auth.encrypt_token", return_value="gAAAAABfenc"),
        ):
            tc = TestClient(create_app())
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "code_x", "shop": shop, "state": state},
                headers={"Authorization": "Bearer token"},
            )

        # Webhook registration failure must not fail the install
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "installed"

    def test_no_org_for_user_returns_403(self) -> None:
        shop = "no-org-store.myshopify.com"
        state = _generate_state(shop, _CLIENT_SECRET)

        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
            patch("app.api.shopify_auth._get_org_for_user", return_value=None),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.post(
                "/auth/shopify/callback",
                json={"code": "code_x", "shop": shop, "state": state},
                headers={"Authorization": "Bearer token"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Begin endpoint tests
# ---------------------------------------------------------------------------


class TestBeginEndpoint:
    def test_returns_state_and_redirect_url(self) -> None:
        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app())
            resp = tc.get(
                "/auth/shopify/begin",
                params={"shop": "mystore.myshopify.com"},
                headers={"Authorization": "Bearer valid_token"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "state" in data
        assert "redirect_url" in data
        assert "mystore.myshopify.com/admin/oauth/authorize" in data["redirect_url"]
        # State returned by begin must be verifiable (proves begin and callback share the secret)
        assert _verify_state(data["state"], "mystore.myshopify.com", _CLIENT_SECRET)

    def test_invalid_shop_returns_400(self) -> None:
        with (
            patch("app.api.shopify_auth.get_settings", return_value=_make_settings()),
            patch(
                "app.api.shopify_auth.verify_supabase_jwt",
                new_callable=AsyncMock,
                return_value=_make_user(),
            ),
        ):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.get(
                "/auth/shopify/begin",
                params={"shop": "notshopify.com"},
                headers={"Authorization": "Bearer valid_token"},
            )
        assert resp.status_code == 400

    def test_no_bearer_returns_401(self) -> None:
        with patch("app.api.shopify_auth.get_settings", return_value=_make_settings()):
            tc = TestClient(create_app(), raise_server_exceptions=False)
            resp = tc.get("/auth/shopify/begin", params={"shop": "s.myshopify.com"})
        assert resp.status_code == 401
