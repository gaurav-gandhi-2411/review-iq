"""POST /webhooks/google/reviews — Cloud Pub/Sub push handler for GBP review notifications.

Google Business Profile does NOT POST review content directly like Shopify's
webhooks. Instead it publishes a NEW_REVIEW notification to a Cloud Pub/Sub topic,
which push-delivers to this endpoint wrapped in a Pub/Sub envelope:

    {"message": {"data": "<base64 JSON>", "messageId": "...", "publishTime": "..."},
     "subscription": "..."}

The base64-decoded `data` contains at minimum the location_name that changed — it
does NOT contain the review's text/rating. So this handler must, after resolving
org_id from google_location_name, refresh an access_token and fetch the actual
review content from the Business Profile API before running extraction.

MULTI-TENANT ROUTING:
  google_business_installations.google_location_name is UNIQUE — the routing key.
  Lookup uses a plain psycopg2 connection (postgres role, no SET ROLE) so it bypasses
  RLS — correct for a system/webhook call, mirroring Shopify's shop_domain routing.

  Unrecognized location_name (never installed or revoked):
    → accept with 200, log and drop — never fall back to a default org.

AUTH:
  Pub/Sub push requests are authenticated via a shared-secret query token
  (?token=...) checked with hmac.compare_digest BEFORE the body is parsed. This is
  a simpler, documented-acceptable Pub/Sub push-auth pattern; OIDC token
  verification (Google's stronger recommended option) can be added later.

TOKEN ENCRYPTION:
  refresh_token_enc in google_business_installations is Fernet-encrypted, using the
  same scheme as Shopify's access_token_enc. Key is GOOGLE_TOKEN_ENCRYPTION_KEY.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
from typing import Any

import psycopg2
import psycopg2.errors
import structlog
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.auth.api_key import ApiKeyContext
from app.core.config import get_settings
from app.core.ingestion.google_business_source import _refresh_access_token, _review_to_review_row

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/google", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Token encryption / decryption — mirrors app/api/webhooks/shopify.py
# ---------------------------------------------------------------------------


def _decrypt_refresh_token(refresh_token_enc: str, encryption_key: str) -> str:
    """Decrypt a Fernet-encrypted Google refresh token.

    Raises ValueError on bad key or tampered ciphertext — caller should drop
    the webhook (never crash, never fall back to plaintext).
    """
    try:
        f = Fernet(encryption_key.encode())
        return f.decrypt(refresh_token_enc.encode()).decode()
    except (InvalidToken, Exception) as exc:
        raise ValueError(f"Token decryption failed: {exc}") from exc


def encrypt_token(refresh_token: str, encryption_key: str) -> str:
    """Encrypt a Google refresh token for storage. Called from the OAuth callback."""
    f = Fernet(encryption_key.encode())
    return f.encrypt(refresh_token.encode()).decode()


# ---------------------------------------------------------------------------
# Installation lookup — service-role (postgres role, bypasses RLS)
# ---------------------------------------------------------------------------


def _get_google_installation_pg(google_location_name: str) -> dict[str, Any] | None:
    """Look up google_business_installations by google_location_name. None if not installed.

    Uses supabase_database_url (postgres role — bypasses RLS). This is correct
    for system/webhook calls. RLS is only active after SET ROLE authenticated.

    UNIQUE(google_location_name) guarantees at most one row — result is never
    ambiguous. Returns None if the table is not yet migrated (UndefinedTable),
    mirroring Shopify's pre-migration-safety catch for defensive consistency
    (the table is live in prod, but this keeps the code path uniform).
    """
    settings = get_settings()
    if not settings.supabase_database_url:
        return None
    conn = psycopg2.connect(settings.supabase_database_url)
    try:
        cur = conn.cursor()
        # No SET ROLE — postgres role bypasses RLS; UNIQUE(google_location_name) = no ambiguity.
        cur.execute(
            "SELECT org_id, google_account_name, refresh_token_enc "
            "FROM public.google_business_installations "
            "WHERE google_location_name = %s AND revoked_at IS NULL",
            (google_location_name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "org_id": str(row[0]),
            "google_account_name": str(row[1]),
            "refresh_token_enc": str(row[2]),
        }
    except psycopg2.errors.UndefinedTable:
        # Migration not yet applied — expected during dev; webhooks dropped safely.
        log.warning("google_webhook.installations_table_missing", location=google_location_name)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pub/Sub envelope parsing
# ---------------------------------------------------------------------------


def _parse_pubsub_push(body: dict[str, Any]) -> dict[str, Any] | None:
    """Base64-decode and JSON-parse the Pub/Sub push envelope's message.data.

    Returns the decoded dict, or None if the envelope is malformed or missing
    the expected fields.
    """
    message = body.get("message")
    if not isinstance(message, dict):
        return None
    data_b64 = message.get("data")
    if not data_b64:
        return None
    try:
        decoded = base64.b64decode(data_b64)
        parsed = json.loads(decoded)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _process_webhook_review(raw_body: bytes) -> None:
    """Background task: parse Pub/Sub envelope → resolve org → fetch review → extract.

    Google's notification payload does not carry review text, so this fetches the
    review resource from the Business Profile API after refreshing an access_token.
    Never raises out of the background task — all failures are logged and dropped.
    """
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewRequest

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        log.error("google_webhook.bad_json")
        return

    payload = _parse_pubsub_push(body)
    if payload is None:
        log.warning("google_webhook.malformed_pubsub_envelope")
        return

    # Google's exact field casing isn't publicly documented in detail — try both.
    location_name = payload.get("location_name") or payload.get("locationName")
    if not location_name:
        log.warning("google_webhook.missing_location_name", payload_keys=list(payload.keys()))
        return

    review_name = payload.get("review_name") or payload.get("reviewName")

    # Resolve org by location_name — the ONLY routing key for webhooks.
    installation = await asyncio.to_thread(_get_google_installation_pg, location_name)
    if installation is None:
        # Unrecognized location (not installed, revoked, or table not yet migrated).
        # Drop silently — never fall back to a default org.
        log.warning("google_webhook.unrecognized_location", location=location_name)
        return

    settings = get_settings()
    if not settings.google_token_encryption_key:
        log.error("google_webhook.no_encryption_key_configured", location=location_name)
        return

    try:
        refresh_token = _decrypt_refresh_token(
            installation["refresh_token_enc"], settings.google_token_encryption_key
        )
    except ValueError:
        log.error(
            "google_webhook.token_decryption_failed",
            location=location_name,
            org_id=installation["org_id"],
        )
        return

    if not review_name:
        log.warning(
            "google_webhook.missing_review_name", location=location_name, org_id=installation["org_id"]
        )
        return

    try:
        access_token = await _refresh_access_token(
            refresh_token, settings.google_client_id, settings.google_client_secret
        )
    except Exception as exc:
        log.error(
            "google_webhook.token_refresh_failed",
            location=location_name,
            org_id=installation["org_id"],
            error=str(exc),
        )
        return

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://mybusiness.googleapis.com/v4/{review_name}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            review = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.error(
            "google_webhook.review_fetch_failed",
            location=location_name,
            org_id=installation["org_id"],
            error=str(exc),
        )
        return

    row = _review_to_review_row(review)
    if row is None:
        log.warning(
            "google_webhook.empty_comment", location=location_name, org_id=installation["org_id"]
        )
        return

    # Synthetic context: webhook extractions don't consume quota or API key slots.
    ctx = ApiKeyContext(
        org_id=installation["org_id"],
        api_key_id=None,          # no API key — system/webhook triggered
        key_name="google_webhook",
        usage_record_id="",       # "" → update_usage_tokens skipped in _run_extraction_v2
    )

    req = ReviewRequest(text=row["text"])
    try:
        await _run_extraction_v2(req, ctx)
        log.info(
            "google_webhook.processed",
            location=location_name,
            source_review_id=row.get("source_review_id"),
            org_id=installation["org_id"],
        )
    except Exception as exc:
        log.error(
            "google_webhook.extraction_failed",
            location=location_name,
            error=str(exc),
            org_id=installation["org_id"],
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/reviews", status_code=status.HTTP_200_OK)
async def google_review_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str = "",
) -> dict[str, str]:
    """Receive a Cloud Pub/Sub push delivery for a NEW_REVIEW notification.

    Returns 200 immediately (Pub/Sub retries on non-2xx). The push-auth token is
    verified BEFORE the body is parsed — unauthenticated POSTs are rejected before
    any processing occurs. Org routing is by google_location_name via
    google_business_installations — no global fallback org.
    """
    settings = get_settings()

    if not settings.google_pubsub_push_token:
        log.error("google_webhook.no_push_token_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Pub/Sub webhook not configured on this server.",
        )

    if not token or not hmac.compare_digest(token, settings.google_pubsub_push_token):
        log.warning("google_webhook.token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing push token.",
        )

    raw_body = await request.body()

    background_tasks.add_task(_process_webhook_review, raw_body)

    log.info("google_webhook.accepted")
    return {"status": "accepted"}
