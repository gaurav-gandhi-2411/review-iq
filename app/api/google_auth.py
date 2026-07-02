"""Google Business Profile (GBP) OAuth install flow.

GET  /auth/google/begin
    Requires: Authorization: Bearer <supabase_jwt>
    Validates the seller's session, generates a stateless CSRF state, returns the
    Google OAuth authorization URL for the frontend to redirect to.

POST /auth/google/callback
    Requires: Authorization: Bearer <supabase_jwt>
    Body: {code, state}
    Called by the FRONTEND after Google redirects back to the SPA. The SPA
    captures the query params from the redirect URL and POSTs them here with
    the seller's active JWT in the Authorization header.

    Verifies (in order):
      1. State CSRF token (proves the redirect matches a begin we initiated)
      2. Seller's Supabase JWT (resolves org_id SERVER-SIDE — never from request params)
    Then: exchanges code for tokens, Fernet-encrypts the refresh_token, discovers all
    GBP locations for the account, upserts one google_business_installations row per
    location, and best-effort registers the Pub/Sub NEW_REVIEW notification setting.

TENANT SECURITY:
    org_id is ALWAYS resolved from verify_supabase_jwt(bearer) → _get_org_for_user(user.id).
    It is NEVER taken from `code`, `state`, or any caller-controlled value.
    A forged or mismatched JWT cannot write an install to a different org.

CSRF:
    State = "{timestamp}:{HMAC-SHA256(str(timestamp), client_secret)}".
    Stateless — no server-side session store required. State expires in 10 minutes.
    Simpler than Shopify's state (no shop-domain-equivalent known at begin time for
    Google — the account/location set is only discovered after the OAuth callback).

TOKEN LIFECYCLE (deliberate delta from Shopify):
    Google access_token expires in ~1 hour; only the refresh_token is persisted
    (Fernet-encrypted, in refresh_token_enc). prompt=consent forces Google to reissue
    a refresh_token even on re-auth — without it, a returning user who already
    granted consent gets no refresh_token in the callback response, which we
    surface as a 502 asking them to revoke access and retry.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import time
from typing import Any

import httpx
import psycopg2
import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.api.webhooks.google import encrypt_token
from app.auth.signup import _get_org_for_user, verify_supabase_jwt
from app.core.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth/google", tags=["google-oauth"])

_STATE_MAX_AGE_SECONDS = 600  # 10 minutes
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
_LOCATIONS_URL_TMPL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account_name}/locations"
_NOTIFICATIONS_URL_TMPL = "https://mybusinessnotifications.googleapis.com/v1/{account_name}/notificationSetting"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class GoogleCallbackBody(BaseModel):
    code: str
    state: str


# ---------------------------------------------------------------------------
# Pure helpers — all independently unit-testable
# ---------------------------------------------------------------------------


def _generate_state(client_secret: str) -> str:
    """Generate a stateless, expiring CSRF token.

    Format: "{unix_timestamp}:{hmac_hex}". Unlike Shopify, there's no shop-domain
    equivalent known at begin time, so this binds only to the timestamp.
    """
    ts = str(int(time.time()))
    mac = _hmac.new(client_secret.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{mac}"


def _verify_state(
    state: str,
    client_secret: str,
    *,
    max_age: int = _STATE_MAX_AGE_SECONDS,
) -> bool:
    """Return True iff state is well-formed, not expired, and HMAC matches.

    Timing-safe comparison via hmac.compare_digest.
    """
    parts = state.split(":", 1)
    if len(parts) != 2:
        return False
    ts_str, received_mac = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if int(time.time()) - ts > max_age:
        return False
    expected_mac = _hmac.new(client_secret.encode(), ts_str.encode(), hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected_mac, received_mac)


# ---------------------------------------------------------------------------
# I/O helpers — mocked in tests
# ---------------------------------------------------------------------------


async def _exchange_code(code: str, settings: Any) -> dict[str, Any]:
    """POST to Google's token endpoint; return the raw token response dict.

    Raises ValueError if no refresh_token is present — this happens when the
    user has already granted consent before without prompt=consent.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.google_webhook_base_url}/auth/google/callback",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    if not data.get("refresh_token"):
        raise ValueError(f"Google returned no refresh_token: {data}")
    return data


async def _list_accounts(access_token: str) -> list[dict[str, Any]]:
    """GET the seller's GBP accounts."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            _ACCOUNTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    accounts: list[dict[str, Any]] = data.get("accounts", [])
    return accounts


async def _list_locations(access_token: str, account_name: str) -> list[dict[str, Any]]:
    """GET the locations belonging to a GBP account."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            _LOCATIONS_URL_TMPL.format(account_name=account_name),
            headers={"Authorization": f"Bearer {access_token}"},
            params={"readMask": "name"},
        )
        resp.raise_for_status()
        data = resp.json()
    locations: list[dict[str, Any]] = data.get("locations", [])
    return locations


async def _register_notifications(account_name: str, access_token: str, settings: Any) -> None:
    """Register the NEW_REVIEW Pub/Sub notification setting for a GBP account.

    Best-effort — a registration failure logs a warning but does NOT fail the install.
    Caller wraps the call site in try/except (mirrors _register_webhook in shopify_auth.py).
    """
    if not settings.google_pubsub_topic:
        log.warning("google_auth.no_pubsub_topic_configured", account=account_name)
        return
    url = _NOTIFICATIONS_URL_TMPL.format(account_name=account_name)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"updateMask": "pubsubTopic,notificationTypes"},
            json={
                "pubsubTopic": settings.google_pubsub_topic,
                "notificationTypes": ["NEW_REVIEW"],
            },
        )
    if resp.status_code in (200, 201):
        log.info("google_auth.notifications_registered", account=account_name)
        return
    log.warning(
        "google_auth.notifications_registration_failed",
        account=account_name,
        http_status=resp.status_code,
        body=resp.text[:200],
    )


def _upsert_installation_pg(
    org_id: str, google_account_name: str, google_location_name: str, refresh_token_enc: str
) -> None:
    """Service-role (postgres, no SET ROLE) upsert into google_business_installations.

    ON CONFLICT (google_location_name) handles re-installs: clears revoked_at and
    rotates the encrypted refresh token. org_id is ALWAYS caller-resolved from the
    verified JWT — never accepted as a user param.
    """
    conn = psycopg2.connect(get_settings().supabase_database_url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.google_business_installations
                (org_id, google_account_name, google_location_name, refresh_token_enc)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (google_location_name)
            DO UPDATE SET
                refresh_token_enc = EXCLUDED.refresh_token_enc,
                revoked_at        = NULL,
                installed_at      = now()
            """,
            (org_id, google_account_name, google_location_name, refresh_token_enc),
        )
        conn.commit()
        log.info(
            "google_auth.installation_saved",
            location=google_location_name,
            org_id=org_id,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/begin")
async def google_oauth_begin(
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, str]:
    """Start the Google OAuth install flow.

    Returns {state, redirect_url}. The frontend must store `state` locally
    (e.g. sessionStorage) and redirect the user to `redirect_url`. Google will
    redirect back to the SPA callback route with code+state.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required.")
    bearer = authorization[len("Bearer "):]
    # Validate the seller is authenticated. Identity is re-verified in the callback.
    await verify_supabase_jwt(bearer)

    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google app not configured.")

    state = _generate_state(settings.google_client_secret)
    redirect_uri = (
        f"{settings.google_webhook_base_url}/auth/google/callback"
        if settings.google_webhook_base_url
        else ""
    )
    scope = "https://www.googleapis.com/auth/business.manage"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.google_client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        f"&scope={scope}"
        "&access_type=offline"
        "&prompt=consent"
        f"&state={state}"
    )
    return {"state": state, "redirect_url": auth_url}


@router.post("/callback")
async def google_oauth_callback(
    body: GoogleCallbackBody,
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, object]:
    """Complete the Google OAuth install.

    Called by the SPA after Google redirects to the frontend callback route.
    The SPA extracts code+state from the URL and POSTs them here with the
    seller's active JWT in the Authorization header.

    Security layers (applied in sequence — first failure short-circuits):
      1. State CSRF token (expires in 10 min)
      2. Supabase JWT → org_id resolution (org_id NEVER from request params)
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required.")
    bearer = authorization[len("Bearer "):]

    settings = get_settings()
    if not settings.google_client_secret or not settings.google_token_encryption_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google app not configured.")

    # 1. Verify state CSRF
    if not _verify_state(body.state, settings.google_client_secret):
        log.warning("google_auth.state_invalid_or_expired")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired state.")

    # 2. Resolve org_id from JWT — NEVER from code, state, or any caller value
    user = await verify_supabase_jwt(bearer)
    org = await asyncio.to_thread(_get_org_for_user, str(user.id))
    if org is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No org found for this user.")
    org_id: str = org["org_id"]

    # Exchange code for tokens
    try:
        token_data = await _exchange_code(body.code, settings)
    except ValueError as exc:
        log.error("google_auth.no_refresh_token", error=str(exc))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Google did not return a refresh token. Please revoke access at "
            "myaccount.google.com/permissions and try connecting again.",
        ) from exc
    except httpx.HTTPError as exc:
        log.error("google_auth.token_exchange_failed", error=str(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Google token exchange failed.") from exc

    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    refresh_token_enc = encrypt_token(refresh_token, settings.google_token_encryption_key)

    # Discover accounts and locations
    accounts = await _list_accounts(access_token)

    locations_installed = 0
    for account in accounts:
        account_name = account.get("name", "")
        if not account_name:
            continue
        locations = await _list_locations(access_token, account_name)
        for location in locations:
            location_name = location.get("name", "")
            if not location_name:
                continue
            await asyncio.to_thread(
                _upsert_installation_pg, org_id, account_name, location_name, refresh_token_enc
            )
            locations_installed += 1

        # Register Pub/Sub notifications — best-effort, never fails the install
        try:
            await _register_notifications(account_name, access_token, settings)
        except Exception as exc:
            log.warning(
                "google_auth.notifications_registration_error",
                account=account_name,
                error=str(exc),
            )

    log.info(
        "google_auth.install_complete",
        org_id=org_id,
        locations_installed=locations_installed,
    )
    return {"status": "installed", "org_id": org_id, "locations_installed": locations_installed}
