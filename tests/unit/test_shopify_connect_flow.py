"""S19 N2b: Shopify connect flow -- redirect design, scopes, fail-loud config, status.

No network, no database: HTTP and psycopg2 are patched. The signed fixtures below are real
Shopify-style HMACs computed with the same algorithm as Shopify's documented callback check,
which is the strongest proof available without a Partner dev store (see
docs/runbooks/shopify-first-install.md).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from app.api.shopify_auth import SHOPIFY_SCOPES, _generate_state
from app.core.config import Settings
from app.main import create_app
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

_SECRET = "connect_flow_client_secret"
_USER = "user-flow-1"
_SHOP = "flow-store.myshopify.com"


def _settings(**overrides: object) -> MagicMock:
    s = MagicMock()
    s.shopify_enabled = True
    s.shopify_client_id = "cid"
    s.shopify_client_secret = _SECRET
    s.shopify_api_version = "2024-10"
    s.shopify_token_encryption_key = Fernet.generate_key().decode()
    s.shopify_webhook_base_url = "https://api.example.com"
    s.shopify_app_url = "https://app.example.com"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _user(user_id: str = _USER) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    return u


def _sign(params: dict[str, str]) -> str:
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return _hmac.new(_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Scopes + redirect design
# ---------------------------------------------------------------------------


def test_scopes_are_read_only() -> None:
    scopes = SHOPIFY_SCOPES.split(",")
    assert scopes == ["read_metaobjects", "read_products"]
    assert not any(s.startswith("write_") for s in scopes)


def test_begin_redirects_to_spa_callback_with_urlencoded_params() -> None:
    with (
        patch("app.api.shopify_auth.get_settings", return_value=_settings()),
        patch("app.api.shopify_auth.verify_supabase_jwt", AsyncMock(return_value=_user())),
    ):
        resp = TestClient(create_app()).get(
            "/auth/shopify/begin",
            params={"shop": _SHOP},
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 200, resp.text
    url = urlparse(resp.json()["redirect_url"])
    q = parse_qs(url.query)
    assert url.netloc == _SHOP and url.path == "/admin/oauth/authorize"
    # The SPA route, NOT the API: only the SPA holds the JWT the callback needs.
    assert q["redirect_uri"] == ["https://app.example.com/shopify/callback"]
    assert q["scope"] == [SHOPIFY_SCOPES]
    assert q["state"] == [resp.json()["state"]]


def test_begin_when_disabled_returns_typed_error() -> None:
    with (
        patch("app.api.shopify_auth.get_settings", return_value=_settings(shopify_enabled=False)),
        patch("app.api.shopify_auth.verify_supabase_jwt", AsyncMock(return_value=_user())),
    ):
        resp = TestClient(create_app()).get(
            "/auth/shopify/begin",
            params={"shop": _SHOP},
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "shopify_disabled"


# ---------------------------------------------------------------------------
# Callback: signed fixtures, extra params, user-bound state
# ---------------------------------------------------------------------------


def _post_callback(body: dict[str, str], *, user_id: str = _USER) -> tuple[int, dict, list]:
    upserts: list[tuple[str, str, str]] = []
    with (
        patch("app.api.shopify_auth.get_settings", return_value=_settings()),
        patch("app.api.shopify_auth.verify_supabase_jwt", AsyncMock(return_value=_user(user_id))),
        patch("app.api.shopify_auth._get_org_for_user", return_value={"org_id": "org-1"}),
        patch("app.api.shopify_auth._exchange_code", AsyncMock(return_value="shpat_x")),
        patch("app.api.shopify_auth.encrypt_token", return_value="enc"),
        patch(
            "app.api.shopify_auth._upsert_installation_pg",
            side_effect=lambda *a: upserts.append(a),
        ),
        patch("app.api.shopify_auth._register_webhook", AsyncMock(return_value=None)),
    ):
        resp = TestClient(create_app()).post(
            "/auth/shopify/callback", json=body, headers={"Authorization": "Bearer t"}
        )
    return resp.status_code, resp.json(), [upserts]


def _genuine_callback_body(state: str) -> dict[str, str]:
    params = {"code": "c0de", "shop": _SHOP, "state": state, "timestamp": "1760000000"}
    params["host"] = "YWRtaW4uc2hvcGlmeS5jb20vc3RvcmUvZmxvdw"  # Shopify adds `host`
    return {**params, "hmac": _sign(params)}


def test_callback_accepts_genuine_shopify_params_including_host() -> None:
    state = _generate_state(_SHOP, _SECRET, _USER)
    code, data, (upserts,) = _post_callback(_genuine_callback_body(state))
    assert code == 200, data
    assert upserts == [("org-1", _SHOP, "enc")]


def test_callback_rejects_tampered_host_param() -> None:
    state = _generate_state(_SHOP, _SECRET, _USER)
    body = _genuine_callback_body(state)
    body["host"] = "attacker"
    code, data, (upserts,) = _post_callback(body)
    assert code == 401 and data["detail"]["code"] == "invalid_hmac"
    assert upserts == []


def test_callback_rejects_state_minted_for_a_different_user() -> None:
    """Login-CSRF: a state begun by user A must not link a store into user B's org."""
    state = _generate_state(_SHOP, _SECRET, "some-other-user")
    code, data, (upserts,) = _post_callback(_genuine_callback_body(state))
    assert code == 401 and data["detail"]["code"] == "invalid_state"
    assert upserts == []


def test_callback_when_disabled_returns_typed_error() -> None:
    state = _generate_state(_SHOP, _SECRET, _USER)
    with patch("app.api.shopify_auth.get_settings", return_value=_settings(shopify_enabled=False)):
        resp = TestClient(create_app()).post(
            "/auth/shopify/callback",
            json=_genuine_callback_body(state),
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == "shopify_disabled"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_disabled_reports_not_enabled_without_touching_db() -> None:
    with (
        patch("app.api.shopify_auth.get_settings", return_value=_settings(shopify_enabled=False)),
        patch("app.api.shopify_auth.verify_supabase_jwt", AsyncMock(return_value=_user())),
        patch("app.api.shopify_auth._list_installations_pg") as listing,
    ):
        resp = TestClient(create_app()).get(
            "/auth/shopify/status", headers={"Authorization": "Bearer t"}
        )
    assert resp.json() == {"enabled": False, "installations": []}
    listing.assert_not_called()


def test_status_lists_only_the_callers_org_installs() -> None:
    row = {"shop_domain": _SHOP, "installed_at": "2026-10-08T00:00:00", "revoked_at": None}
    with (
        patch("app.api.shopify_auth.get_settings", return_value=_settings()),
        patch("app.api.shopify_auth.verify_supabase_jwt", AsyncMock(return_value=_user())),
        patch("app.api.shopify_auth._get_org_for_user", return_value={"org_id": "org-9"}),
        patch("app.api.shopify_auth._list_installations_pg", return_value=[row]) as listing,
    ):
        resp = TestClient(create_app()).get(
            "/auth/shopify/status", headers={"Authorization": "Bearer t"}
        )
    assert resp.json() == {"enabled": True, "installations": [row]}
    listing.assert_called_once_with("org-9")  # org from the JWT-resolved user only


def test_status_requires_bearer() -> None:
    with patch("app.api.shopify_auth.get_settings", return_value=_settings()):
        resp = TestClient(create_app()).get("/auth/shopify/status")
    assert resp.status_code == 401 and resp.json()["detail"]["code"] == "auth_required"


# ---------------------------------------------------------------------------
# Fail-loud config (the blank-page lesson, backend side)
# ---------------------------------------------------------------------------


def _full_env() -> dict[str, str]:
    return {
        "SHOPIFY_ENABLED": "true",
        "SHOPIFY_CLIENT_ID": "cid",
        "SHOPIFY_CLIENT_SECRET": "sec",
        "SHOPIFY_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SHOPIFY_APP_URL": "https://app.example.com",
        "SHOPIFY_WEBHOOK_BASE_URL": "https://api.example.com",
    }


def _build(env: dict[str, str]) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_shopify_is_off_by_default() -> None:
    assert _build({}).shopify_enabled is False


def test_enabled_with_complete_config_starts() -> None:
    assert _build(_full_env()).shopify_enabled is True


@pytest.mark.parametrize(
    "missing",
    [
        "SHOPIFY_CLIENT_ID",
        "SHOPIFY_CLIENT_SECRET",
        "SHOPIFY_TOKEN_ENCRYPTION_KEY",
        "SHOPIFY_APP_URL",
        "SHOPIFY_WEBHOOK_BASE_URL",
    ],
)
def test_enabled_with_missing_setting_refuses_to_start_naming_it(missing: str) -> None:
    env = _full_env()
    del env[missing]
    with pytest.raises(ValueError, match=missing):
        _build(env)


def test_enabled_with_bad_fernet_key_or_url_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="valid Fernet key"):
        _build({**_full_env(), "SHOPIFY_TOKEN_ENCRYPTION_KEY": "not-a-key"})
    with pytest.raises(ValueError, match="https://"):
        _build({**_full_env(), "SHOPIFY_APP_URL": "http://app.example.com"})
    with pytest.raises(ValueError, match="no trailing slash"):
        _build({**_full_env(), "SHOPIFY_WEBHOOK_BASE_URL": "https://api.example.com/"})
