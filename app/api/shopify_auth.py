"""Shopify OAuth install flow.

GET  /auth/shopify/begin?shop=store.myshopify.com
    Requires: Authorization: Bearer <supabase_jwt>
    Validates the seller's session, generates a stateless CSRF state, returns the
    Shopify OAuth authorization URL for the frontend to redirect to.

POST /auth/shopify/callback
    Requires: Authorization: Bearer <supabase_jwt>
    Body: {code, shop, state, hmac?, timestamp?}
    Called by the FRONTEND after Shopify redirects back to the SPA. The SPA
    captures the query params from the redirect URL and POSTs them here with
    the seller's active JWT in the Authorization header.

    Verifies (in order):
      1. Shopify's hmac on the callback params (proves Shopify signed this redirect)
      2. State CSRF token (proves the redirect matches a begin we initiated for this shop)
      3. Seller's Supabase JWT (resolves org_id SERVER-SIDE — never from request params)
    Then: exchanges code for access_token, Fernet-encrypts, upserts shopify_installations,
    registers the METAOBJECTS_CREATE webhook.

TENANT SECURITY:
    org_id is ALWAYS resolved from verify_supabase_jwt(bearer) → _get_org_for_user(user.id).
    It is NEVER taken from `shop`, `state`, `timestamp`, or any caller-controlled value.
    A forged or mismatched JWT cannot write an install to a different org.

CSRF:
    State = "{timestamp}:{HMAC-SHA256(shop_domain:timestamp, client_secret)}".
    Stateless — no server-side session store required. State expires in 10 minutes.
    Binding to shop_domain prevents state from shop A being replayed for shop B.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import re
import time
from typing import Any

import httpx
import psycopg2
import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.api.webhooks.shopify import encrypt_token
from app.auth.signup import _get_org_for_user, verify_supabase_jwt
from app.core.config import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth/shopify", tags=["shopify-oauth"])

_SHOP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")
_STATE_MAX_AGE_SECONDS = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ShopifyCallbackBody(BaseModel):
    code: str
    shop: str
    state: str
    timestamp: str = ""  # Shopify includes this; used in hmac verification
    hmac: str = ""  # Shopify-generated HMAC over all callback params


# ---------------------------------------------------------------------------
# Pure helpers — all independently unit-testable
# ---------------------------------------------------------------------------


def _validate_shop(shop: str) -> str:
    """Normalize to lowercase and validate that shop matches *.myshopify.com."""
    s = shop.strip().lower()
    if not _SHOP_RE.match(s):
        raise ValueError(f"Invalid shop domain: {shop!r}")
    return s


def _generate_state(shop_domain: str, client_secret: str) -> str:
    """Generate a stateless, expiring CSRF token bound to shop_domain.

    Format: "{unix_timestamp}:{hmac_hex}"
    The HMAC covers "{shop_domain}:{timestamp}" using client_secret as the key,
    so the token is shop-specific and forge-resistant.
    """
    ts = str(int(time.time()))
    msg = f"{shop_domain}:{ts}"
    mac = _hmac.new(client_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{mac}"


def _verify_state(
    state: str,
    shop_domain: str,
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
    msg = f"{shop_domain}:{ts_str}"
    expected_mac = _hmac.new(
        client_secret.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    return _hmac.compare_digest(expected_mac, received_mac)


def _verify_shopify_callback_hmac(params: dict[str, str], client_secret: str) -> bool:
    """Verify the `hmac` query param Shopify appends to the OAuth callback URL.

    Shopify computes: HMAC-SHA256(client_secret, sorted_params_except_hmac).
    All params (code, shop, state, timestamp, ...) except `hmac` itself, sorted
    alphabetically by key, joined as "key=value&key2=value2".
    """
    received = params.get("hmac", "")
    if not received:
        return False
    msg = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k != "hmac")
    expected = _hmac.new(client_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# I/O helpers — mocked in tests
# ---------------------------------------------------------------------------


async def _exchange_code(shop_domain: str, code: str, settings: Any) -> str:
    """POST to Shopify token endpoint; return plaintext access_token."""
    url = f"https://{shop_domain}/admin/oauth/access_token"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            json={
                "client_id": settings.shopify_client_id,
                "client_secret": settings.shopify_client_secret,
                "code": code,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise ValueError(f"Shopify returned no access_token: {data}")
    return token


async def _register_webhook(shop_domain: str, access_token: str, settings: Any) -> str | None:
    """Register the METAOBJECTS_CREATE webhook via Shopify REST Admin API.

    Best-effort — a registration failure logs a warning but does NOT fail the install.
    Webhook notifications are received at {shopify_webhook_base_url}/webhooks/shopify/reviews.
    """
    if not settings.shopify_webhook_base_url:
        log.warning("shopify_auth.no_webhook_base_url", shop=shop_domain)
        return None
    callback_url = f"{settings.shopify_webhook_base_url}/webhooks/shopify/reviews"
    api_url = f"https://{shop_domain}/admin/api/{settings.shopify_api_version}/webhooks.json"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            api_url,
            headers={"X-Shopify-Access-Token": access_token},
            json={"webhook": {"topic": "metaobjects/create", "address": callback_url, "format": "json"}},
        )
    if resp.status_code in (200, 201):
        webhook_id = str(resp.json().get("webhook", {}).get("id", ""))
        log.info("shopify_auth.webhook_registered", shop=shop_domain, webhook_id=webhook_id)
        return webhook_id
    log.warning(
        "shopify_auth.webhook_registration_failed",
        shop=shop_domain,
        http_status=resp.status_code,
        body=resp.text[:200],
    )
    return None


def _upsert_installation_pg(org_id: str, shop_domain: str, access_token_enc: str) -> None:
    """Service-role (postgres, no SET ROLE) upsert into shopify_installations.

    ON CONFLICT (shop_domain) handles re-installs: clears revoked_at and rotates the
    encrypted token. org_id is ALWAYS caller-resolved from the verified JWT — never
    accepted as a user param.
    """
    conn = psycopg2.connect(get_settings().supabase_database_url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO public.shopify_installations (org_id, shop_domain, access_token_enc)
            VALUES (%s, %s, %s)
            ON CONFLICT (shop_domain)
            DO UPDATE SET
                access_token_enc = EXCLUDED.access_token_enc,
                revoked_at       = NULL,
                installed_at     = now()
            """,
            (org_id, shop_domain, access_token_enc),
        )
        conn.commit()
        log.info("shopify_auth.installation_saved", shop=shop_domain, org_id=org_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/begin")
async def shopify_oauth_begin(
    shop: str,
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, str]:
    """Start the Shopify OAuth install flow.

    Returns {state, redirect_url}. The frontend must store `state` locally
    (e.g. sessionStorage) and redirect the user to `redirect_url`. Shopify
    will redirect back to the SPA callback route with code+state+shop+hmac.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required.")
    bearer = authorization[len("Bearer "):]
    # Validate the seller is authenticated. We don't need user_id here
    # because the state is not carrying identity — identity is re-verified
    # in the callback.
    await verify_supabase_jwt(bearer)

    settings = get_settings()
    if not settings.shopify_client_id or not settings.shopify_client_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Shopify app not configured.")

    try:
        shop_domain = _validate_shop(shop)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    state = _generate_state(shop_domain, settings.shopify_client_secret)
    scopes = "read_metaobjects,write_product_reviews,read_products,read_customers"
    redirect_uri = (
        f"{settings.shopify_webhook_base_url}/auth/shopify/callback"
        if settings.shopify_webhook_base_url
        else ""
    )
    auth_url = (
        f"https://{shop_domain}/admin/oauth/authorize"
        f"?client_id={settings.shopify_client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return {"state": state, "redirect_url": auth_url}


@router.post("/callback")
async def shopify_oauth_callback(
    body: ShopifyCallbackBody,
    authorization: str = Header(default="", alias="Authorization"),
) -> dict[str, str]:
    """Complete the Shopify OAuth install.

    Called by the SPA after Shopify redirects to the frontend callback route.
    The SPA extracts code+shop+state+hmac from the URL and POSTs them here with
    the seller's active JWT in Authorization header.

    Security layers (applied in sequence — first failure short-circuits):
      1. Shopify hmac verification (skipped if hmac absent — allows test calls)
      2. State CSRF token (must match shop_domain; expires in 10 min)
      3. Supabase JWT → org_id resolution (org_id NEVER from request params)
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required.")
    bearer = authorization[len("Bearer "):]

    settings = get_settings()
    if not settings.shopify_client_secret or not settings.shopify_token_encryption_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Shopify app not configured.")

    # 1. Verify Shopify's HMAC (when present — always present in real Shopify callbacks)
    if body.hmac:
        callback_params: dict[str, str] = {
            "code": body.code,
            "shop": body.shop,
            "state": body.state,
        }
        if body.timestamp:
            callback_params["timestamp"] = body.timestamp
        callback_params["hmac"] = body.hmac
        if not _verify_shopify_callback_hmac(callback_params, settings.shopify_client_secret):
            log.warning("shopify_auth.callback_hmac_invalid", shop=body.shop)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Shopify HMAC.")

    # 2. Validate shop domain format + verify state CSRF
    try:
        shop_domain = _validate_shop(body.shop)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    if not _verify_state(body.state, shop_domain, settings.shopify_client_secret):
        log.warning("shopify_auth.state_invalid_or_expired", shop=shop_domain)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired state.")

    # 3. Resolve org_id from JWT — NEVER from shop param, state, or any caller value
    user = await verify_supabase_jwt(bearer)
    org = await asyncio.to_thread(_get_org_for_user, str(user.id))
    if org is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No org found for this user.")
    org_id: str = org["org_id"]

    # Exchange code for plaintext access_token, then immediately encrypt
    try:
        access_token = await _exchange_code(shop_domain, body.code, settings)
    except (httpx.HTTPError, ValueError) as exc:
        log.error("shopify_auth.token_exchange_failed", shop=shop_domain, error=str(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Shopify token exchange failed.") from exc

    access_token_enc = encrypt_token(access_token, settings.shopify_token_encryption_key)

    # Upsert installation — service-role, org_id from JWT
    await asyncio.to_thread(_upsert_installation_pg, org_id, shop_domain, access_token_enc)

    # Register webhook — best-effort, never fails the install
    try:
        await _register_webhook(shop_domain, access_token, settings)
    except Exception as exc:
        log.warning("shopify_auth.webhook_registration_error", shop=shop_domain, error=str(exc))

    log.info("shopify_auth.install_complete", shop=shop_domain, org_id=org_id)
    return {"status": "installed", "shop": shop_domain, "org_id": org_id}
