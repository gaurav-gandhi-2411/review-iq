"""BFF Judge.me connector endpoints: connect, status, disconnect (docs/specs/judgeme-ingestion.md
section 6). Feature-flagged: every route is 404 unless ENABLE_JUDGEME_CONNECTOR is true.

Security invariants:
  * org_id comes from the verified session context only, never from the body;
  * shop_domain is validated before any outbound request (SSRF);
  * the token is Fernet-encrypted before it reaches the database, never echoed, never logged, and
    kept out of validation-error echoes (``repr=False`` + manual length checks);
  * a stateless-mode org is refused (409) because staging review text on the queue is not
    stateless (ADR 0025, same rule as POST /v2/ingest/csv).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.webhooks.shopify import encrypt_token
from app.auth.api_key import ApiKeyContext
from app.auth.session import require_session, require_session_read
from app.core.config import get_settings
from app.core.ingestion.judgeme_client import (
    AuthRejected,
    AuthTransport,
    ClientRequestError,
    InvalidShopDomain,
    JudgeMeClient,
    JudgeMeError,
    RateLimited,
    ShopNotFound,
    validate_shop_domain,
)
from app.core.ingestion.judgeme_store import (
    ShopConnectedElsewhere,
    count_tracked_pg,
    delete_state_pg,
    get_installation_pg,
    revoke_installation_pg,
    upsert_installation_pg,
)


def require_judgeme_enabled() -> None:
    if not get_settings().enable_judgeme_connector:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


# Router-level dependency: the flag check runs before auth, body parsing and every handler.
router = APIRouter(dependencies=[Depends(require_judgeme_enabled)])
log = structlog.get_logger(__name__)


def _err(code: int, error: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(code, {"code": error, "message": message, **extra})


class ConnectBody(BaseModel):
    shop_domain: str = Field(default="", repr=False)
    api_token: str = Field(default="", repr=False)  # repr=False: never in logs or error echoes


def make_client(shop_domain: str, token: str, transport: AuthTransport) -> JudgeMeClient:
    """Seam for tests (they return a client on a MockTransport)."""
    return JudgeMeClient(shop_domain, token, auth_transport=transport, per_page=1)


async def _probe(shop_domain: str, token: str) -> AuthTransport:
    """One per_page=1 call. Header first; a 401/403 is retried once on the VERIFIED query
    transport (the header is BELIEVED, spec F14) and the working transport is returned."""
    try:
        await make_client(shop_domain, token, "header").fetch_page(1)
        return "header"
    except AuthRejected:
        await make_client(shop_domain, token, "query").fetch_page(1)  # AuthRejected -> bad token
        return "query"


@router.post("/judgeme/connect", status_code=status.HTTP_201_CREATED)
async def judgeme_connect(
    body: ConnectBody,
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> dict[str, str]:
    settings = get_settings()
    try:
        shop = validate_shop_domain(body.shop_domain)
    except InvalidShopDomain as exc:
        raise _err(422, exc.code, "shop_domain must look like store-name.myshopify.com") from exc
    token = body.api_token.strip()
    if not 8 <= len(token) <= 512:
        raise _err(422, "judgeme_bad_token", "Paste the private API token from Judge.me.")
    if ctx.retention_mode != "retained":
        raise _err(
            409,
            "retention_required",
            "Judge.me sync stores review text on the ingest queue, so it needs retained mode.",
        )
    if not settings.judgeme_token_encryption_key:
        raise _err(503, "judgeme_not_configured", "Judge.me sync is not configured.")
    try:
        transport = await _probe(shop, token)
    except AuthRejected as exc:
        raise _err(
            422,
            "judgeme_bad_token",
            "Judge.me rejected this token. Check the private token and that your Judge.me plan "
            "includes API access.",
        ) from exc
    except ShopNotFound as exc:
        raise _err(422, exc.code, "Judge.me does not know this shop domain.") from exc
    except RateLimited as exc:
        raise _err(
            429, exc.code, "Judge.me is rate limiting; retry shortly.", retry_after=exc.retry_after
        ) from exc
    except (ClientRequestError, JudgeMeError) as exc:
        raise _err(
            502, getattr(exc, "code", "judgeme_unreachable"), "Judge.me is unreachable."
        ) from exc

    token_enc = encrypt_token(token, settings.judgeme_token_encryption_key)
    try:
        await asyncio.to_thread(upsert_installation_pg, ctx.org_id, shop, token_enc, transport)
    except ShopConnectedElsewhere as exc:
        raise _err(
            409, "shop_connected_elsewhere", "This shop is connected to another account."
        ) from exc
    log.info("judgeme.connected", org_id=ctx.org_id, shop=shop, auth_transport=transport)
    return {"status": "connected", "shop_domain": shop}


@router.get("/judgeme/status")
async def judgeme_status(
    ctx: Annotated[ApiKeyContext, Depends(require_session_read)],
) -> dict[str, Any]:
    inst = await asyncio.to_thread(get_installation_pg, ctx.org_id)
    out: dict[str, Any] = {
        "status": "never_connected",
        "shop_domain": None,
        "last_sync_at": None,
        "last_sync_status": None,
        "last_error_code": None,
        "reviews_tracked": 0,
        "needs_action": None,
    }
    if inst is None:
        return out
    revoked = inst["revoked_at"] is not None
    if revoked:
        state = "disconnected" if inst["revoked_reason"] == "disconnected" else "revoked"
        action = None if state == "disconnected" else "reconnect"
    elif ctx.retention_mode != "retained":
        state, action = "paused_stateless", "change_retention_mode"
    else:
        state, action = "active", None
    out.update(
        status=state,
        shop_domain=inst["shop_domain"],
        last_sync_at=inst["last_sync_at"].isoformat() if inst["last_sync_at"] else None,
        last_sync_status=inst["last_sync_status"],
        last_error_code=inst["last_sync_error"],
        needs_action=action,
    )
    if not revoked:
        out["reviews_tracked"] = await asyncio.to_thread(
            count_tracked_pg, ctx.org_id, str(inst["id"])
        )
    return out


@router.delete("/judgeme/connection", status_code=status.HTTP_204_NO_CONTENT)
async def judgeme_disconnect(
    ctx: Annotated[ApiKeyContext, Depends(require_session)],
) -> None:
    """Wipe our copy of the token and stop syncing. Judge.me's token itself cannot be rotated by
    the merchant (spec F1): if they think it leaked they must contact Judge.me support."""
    inst = await asyncio.to_thread(get_installation_pg, ctx.org_id)
    if inst is None:
        raise _err(404, "judgeme_not_connected", "No Judge.me connection to remove.")
    inst_id = str(inst["id"])
    await asyncio.to_thread(revoke_installation_pg, inst_id, ctx.org_id, "disconnected")
    await asyncio.to_thread(delete_state_pg, ctx.org_id, inst_id)
    log.info("judgeme.disconnected", org_id=ctx.org_id)
