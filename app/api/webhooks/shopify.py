"""POST /webhooks/shopify/reviews — real-time Shopify product review ingestion.

Receives METAOBJECTS_CREATE webhooks filtered by type:product_review.
Verifies the Shopify HMAC-SHA256 signature before processing.

MULTI-TENANT ROUTING:
  Every webhook carries X-Shopify-Shop-Domain. The handler looks up
  shopify_installations WHERE shop_domain = <header> to get the org_id for that
  store. UNIQUE(shop_domain) on that table guarantees one shop → one org — the
  result is always 0 or 1 row, never ambiguous.

  Lookup uses a plain psycopg2 connection (postgres role, no SET ROLE authenticated)
  so it bypasses RLS — correct for a system/webhook call. The shop_domain UNIQUE
  constraint is the anti-ambiguity gate; RLS is the per-org read fence for user calls.

  Unrecognized shop (store not installed or installation revoked):
    → accept with 200 (Shopify retries on non-2xx), log and drop — never fall back
      to a default org.

TOKEN ENCRYPTION:
  access_token_enc in shopify_installations is Fernet-encrypted
  (AES-128-CBC + HMAC-SHA256). Key is SHOPIFY_TOKEN_ENCRYPTION_KEY env var
  (44-char URL-safe base64, generated with Fernet.generate_key()).
  Key lives in Google Secret Manager for prod, .env.local (gitignored) for dev.
  Never committed, never in .env.

WEBHOOK SETUP (GG must configure in the Shopify Partner dashboard or via the
GraphQL Admin API webhookSubscriptionCreate mutation):
  Topic:   METAOBJECTS_CREATE
  Filter:  type:product_review
  Format:  JSON
  Address: https://<your-api-domain>/webhooks/shopify/reviews
  API ver: 2024-10 or later
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from typing import Any

import psycopg2
import psycopg2.errors
import structlog
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.auth.api_key import ApiKeyContext
from app.core.config import get_settings
from app.core.ingestion.shopify_source import _node_to_review_row
from app.core.storage_pg import _set_tenant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/shopify", tags=["webhooks"])


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


def _verify_shopify_hmac(raw_body: bytes, hmac_header: str, client_secret: str) -> bool:
    """Return True iff X-Shopify-Hmac-Sha256 matches HMAC-SHA256(client_secret, body).

    Uses hmac.compare_digest to prevent timing attacks.
    """
    expected = base64.b64encode(
        hmac.new(client_secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, hmac_header)


# ---------------------------------------------------------------------------
# Token encryption / decryption
# ---------------------------------------------------------------------------


def _build_fernet(encryption_key: str) -> MultiFernet:
    """Build a (Multi)Fernet from one or more comma-separated keys.

    Key-rotation support (audit finding #6): SHOPIFY_TOKEN_ENCRYPTION_KEY can hold
    a single key (today's exact format, unchanged) or "new_key,old_key,..." to
    rotate. encrypt_token always uses the FIRST key; decrypt_token tries each key
    in order, so existing ciphertext keeps decrypting through a rotation -- only
    remove an old key from the list once you're sure nothing still needs it (no
    batch re-encryption job exists; a token is only re-encrypted if its merchant
    re-authorizes). See ops/runbooks/secret-rotation.md for the rotation procedure.
    """
    keys = [Fernet(k.strip().encode()) for k in encryption_key.split(",") if k.strip()]
    return MultiFernet(keys)


def _decrypt_token(access_token_enc: str, encryption_key: str) -> str:
    """Decrypt a Fernet-encrypted Shopify access token.

    Raises ValueError on bad key or tampered ciphertext — caller should drop
    the webhook (never crash, never fall back to plaintext).
    """
    try:
        f = _build_fernet(encryption_key)
        return f.decrypt(access_token_enc.encode()).decode()
    except (InvalidToken, Exception) as exc:
        raise ValueError(f"Token decryption failed: {exc}") from exc


def encrypt_token(access_token: str, encryption_key: str) -> str:
    """Encrypt a Shopify access token for storage. Called from the OAuth callback."""
    f = _build_fernet(encryption_key)
    return f.encrypt(access_token.encode()).decode()


# ---------------------------------------------------------------------------
# Installation lookup — two-step, no bypass-holding role needed (BYPASSRLS remediation)
# ---------------------------------------------------------------------------


def _get_shopify_installation_pg(shop_domain: str) -> dict[str, Any] | None:
    """Look up shopify_installations by shop_domain. Returns None if not installed.

    Bug fixed 2026-08-01: previously a single query relying on the connecting role
    (review_iq_app) holding BYPASSRLS -- see
    supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql. Now two
    steps, NEITHER of which needs review_iq_app to hold any bypass:

    1. Resolve org_id via public.resolve_org_for_shopify_shop(), a narrow SECURITY
       DEFINER function that returns ONLY org_id (never the row, never the encrypted
       token) -- review_iq_app has EXECUTE on it, nothing more.
    2. With org_id now known, fetch the actual row via a normal, _set_tenant()-scoped
       query -- properly RLS-protected like every other tenant-scoped read in this repo.

    UNIQUE(shop_domain) still guarantees step 1 returns at most one org_id --
    unambiguous, unchanged from before. Returns None if the table is not yet migrated
    (UndefinedTable) so webhooks are safely dropped before the migration is applied.
    """
    settings = get_settings()
    if not settings.supabase_database_url:
        return None
    conn = psycopg2.connect(settings.supabase_database_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT public.resolve_org_for_shopify_shop(%s)", (shop_domain,))
        row = cur.fetchone()
        org_id = row[0] if row else None
        if org_id is None:
            return None

        _set_tenant(cur, str(org_id))
        cur.execute(
            "SELECT access_token_enc FROM public.shopify_installations "
            "WHERE shop_domain = %s AND revoked_at IS NULL",
            (shop_domain,),
        )
        detail = cur.fetchone()
        if detail is None:
            return None
        return {"org_id": str(org_id), "access_token_enc": str(detail[0])}
    except psycopg2.errors.UndefinedTable:
        # Migration not yet applied — expected during dev; webhooks dropped safely.
        log.warning("shopify_webhook.installations_table_missing", shop=shop_domain)
        return None
    except psycopg2.errors.UndefinedFunction:
        # resolve_org_for_shopify_shop not yet created — same fail-safe as UndefinedTable,
        # expected before the accompanying migration's cutover (see the runbook).
        log.warning("shopify_webhook.resolve_function_missing", shop=shop_domain)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Payload parsing (REST webhook format for metaobject events)
# ---------------------------------------------------------------------------


def _parse_webhook_payload(body: dict[str, Any]) -> dict[str, Any] | None:
    """Extract metaobject fields from the Shopify REST webhook payload.

    Returns a node dict compatible with _node_to_review_row, or None if
    the payload is not a product_review metaobject.
    """
    if body.get("type") != "product_review":
        return None

    raw_fields = body.get("fields", [])

    # Shopify REST webhook delivers fields as a flat array of {key, value} dicts.
    # _node_to_review_row expects the GraphQL shape (same structure without reference).
    node: dict[str, Any] = {
        "id": str(body.get("admin_graphql_api_id") or body.get("id") or ""),
        "fields": raw_fields,
    }
    return node


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def _process_webhook_review(
    raw_body: bytes,
    shop_domain: str,
) -> None:
    """Background task: look up org by shop → decrypt token → extract → store → alert."""
    from app.api.v2.extract import _run_extraction_v2
    from app.core.schemas import ReviewRequest

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        log.error("shopify_webhook.bad_json", shop=shop_domain)
        return

    node = _parse_webhook_payload(body)
    if node is None:
        log.info(
            "shopify_webhook.skipped_non_review",
            shop=shop_domain,
            metaobject_type=body.get("type"),
        )
        return

    row = _node_to_review_row(node)
    if row is None:
        log.warning("shopify_webhook.empty_body", shop=shop_domain, node_id=node.get("id"))
        return

    # Resolve org by shop domain — the ONLY routing key for webhooks.
    installation = await asyncio.to_thread(_get_shopify_installation_pg, shop_domain)
    if installation is None:
        # Unrecognized store (not installed, revoked, or table not yet migrated).
        # Drop silently — never fall back to a default org.
        log.warning(
            "shopify_webhook.unrecognized_store",
            shop=shop_domain,
            node_id=node.get("id"),
        )
        return

    settings = get_settings()
    if not settings.shopify_token_encryption_key:
        log.error("shopify_webhook.no_encryption_key_configured", shop=shop_domain)
        return

    try:
        _decrypt_token(installation["access_token_enc"], settings.shopify_token_encryption_key)
    except ValueError:
        log.error(
            "shopify_webhook.token_decryption_failed",
            shop=shop_domain,
            org_id=installation["org_id"],
        )
        return

    # Synthetic context: webhook extractions don't consume quota or API key slots.
    ctx = ApiKeyContext(
        org_id=installation["org_id"],
        api_key_id=None,  # no API key — system/webhook triggered
        key_name="shopify_webhook",
        usage_record_id="",  # "" → update_usage_tokens skipped in _run_extraction_v2
    )

    req = ReviewRequest(text=row["text"])
    try:
        await _run_extraction_v2(req, ctx)
        log.info(
            "shopify_webhook.processed",
            shop=shop_domain,
            source_review_id=row.get("source_review_id"),
            org_id=installation["org_id"],
        )
    except Exception as exc:
        log.error(
            "shopify_webhook.extraction_failed",
            shop=shop_domain,
            error=str(exc),
            org_id=installation["org_id"],
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/reviews", status_code=status.HTTP_200_OK)
async def shopify_review_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(...),
    x_shopify_shop_domain: str = Header(...),
    x_shopify_topic: str = Header(""),
) -> dict[str, str]:
    """Receive METAOBJECTS_CREATE webhook from Shopify.

    Returns 200 immediately (Shopify retries on non-2xx). Extraction runs in
    the background so the response is never delayed by LLM latency.

    Signature verification happens BEFORE the body is parsed — unsigned POSTs
    are rejected before any processing occurs. Org routing is by shop_domain via
    shopify_installations — no global fallback key.
    """
    settings = get_settings()

    if not settings.shopify_client_secret:
        log.error("shopify_webhook.no_client_secret_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shopify webhook not configured on this server.",
        )

    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256, settings.shopify_client_secret):
        log.warning(
            "shopify_webhook.hmac_rejected",
            shop=x_shopify_shop_domain,
            topic=x_shopify_topic,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="HMAC signature verification failed.",
        )

    background_tasks.add_task(
        _process_webhook_review,
        raw_body,
        x_shopify_shop_domain,
    )

    log.info(
        "shopify_webhook.accepted",
        shop=x_shopify_shop_domain,
        topic=x_shopify_topic,
    )
    return {"status": "accepted"}
