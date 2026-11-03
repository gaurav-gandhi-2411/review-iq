"""S19 N2b: Shopify post-install backfill -- dedupe, cap, durable-queue-only, never raises."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.shopify_auth import _generate_state
from app.core.ingestion.base import SourceError
from app.core.ingestion.shopify_backfill import BACKFILL_ROW_CAP, enqueue_backfill, plan_rows
from app.core.ingestion.shopify_source import ShopifySource
from app.core.schemas import ReviewRequest
from app.main import create_app
from fastapi.testclient import TestClient

_SHOP = "flow-store.myshopify.com"


# ---------------------------------------------------------------------------
# Backfill: dedupe, cap, queue-only, never raises
# ---------------------------------------------------------------------------


def _row(text: str, product: str | None = "Buds") -> dict[str, str]:
    r = {"text": text}
    if product:
        r["product"] = product
    return r


def test_plan_rows_drops_in_batch_and_already_extracted_duplicates() -> None:
    done = ReviewRequest(text="Already done").input_hash()
    plan = plan_rows(
        [_row("Fresh one"), _row("Fresh one"), _row("Already done"), _row("Fresh two")],
        {done},
    )
    assert [r["text"] for r in plan.rows] == ["Fresh one", "Fresh two"]
    assert plan.fetched == 4 and plan.skipped_duplicate == 2


_P = "app.core.ingestion.shopify_backfill"


@pytest.mark.asyncio
async def test_enqueue_backfill_stages_through_the_durable_queue_only() -> None:
    fetch = AsyncMock(return_value=[_row("A review"), _row("B review", None)])
    with (
        patch(f"{_P}.get_org_retention_pg", return_value=("retained", 90)),
        patch.object(ShopifySource, "fetch_reviews", fetch),
        patch(f"{_P}.existing_input_hashes_pg", return_value=set()),
        patch(f"{_P}.create_batch_job_pg") as create,
        patch(f"{_P}.enqueue_batch_job_rows_pg") as enqueue,
        patch(f"{_P}.update_batch_job_pg") as update,
    ):
        job_id = await enqueue_backfill("org-1", _SHOP, "tok", "2024-10")
    assert job_id is not None
    fetch.assert_awaited_once_with(limit=BACKFILL_ROW_CAP)
    assert create.call_args.args[:3] == ("org-1", job_id, 2)
    assert enqueue.call_args.args == ("org-1", job_id, ["A review", "B review"], ["Buds", None])
    assert update.call_args.kwargs == {"status": "processing"}


@pytest.mark.asyncio
async def test_enqueue_backfill_is_idempotent_when_everything_already_extracted() -> None:
    texts = ["A review", "B review"]
    hashes = {ReviewRequest(text=t).input_hash() for t in texts}
    with (
        patch(f"{_P}.get_org_retention_pg", return_value=("retained", 90)),
        patch.object(
            ShopifySource, "fetch_reviews", AsyncMock(return_value=[_row(t) for t in texts])
        ),
        patch(f"{_P}.existing_input_hashes_pg", return_value=hashes),
        patch(f"{_P}.create_batch_job_pg") as create,
        patch(f"{_P}.enqueue_batch_job_rows_pg") as enqueue,
    ):
        assert await enqueue_backfill("org-1", _SHOP, "tok", "2024-10") is None
    create.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_backfill_skips_stateless_orgs_without_fetching() -> None:
    fetch = AsyncMock()
    with (
        patch(f"{_P}.get_org_retention_pg", return_value=("stateless", None)),
        patch.object(ShopifySource, "fetch_reviews", fetch),
    ):
        assert await enqueue_backfill("org-1", _SHOP, "tok", "2024-10") is None
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_backfill_swallows_source_and_db_errors() -> None:
    with (
        patch(f"{_P}.get_org_retention_pg", return_value=("retained", 90)),
        patch.object(ShopifySource, "fetch_reviews", AsyncMock(side_effect=SourceError("403"))),
    ):
        assert await enqueue_backfill("org-1", _SHOP, "tok", "2024-10") is None
    with patch(f"{_P}.get_org_retention_pg", side_effect=RuntimeError("db down")):
        assert await enqueue_backfill("org-1", _SHOP, "tok", "2024-10") is None


@pytest.mark.asyncio
async def test_fetch_reviews_limit_stops_paging_and_truncates() -> None:
    def node(i: int) -> dict:
        return {"node": {"id": f"gid://{i}", "fields": [{"key": "body", "value": f"review {i}"}]}}

    page = {
        "data": {
            "metaobjects": {
                "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                "edges": [node(i) for i in range(50)],
            }
        }
    }
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=page)
    with patch("httpx.AsyncClient") as cls:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=None)
        ctx.post = AsyncMock(return_value=resp)
        cls.return_value = ctx
        rows = await ShopifySource(_SHOP, "tok").fetch_reviews(limit=10)
    assert len(rows) == 10
    assert ctx.post.await_count == 1  # hasNextPage was True but the cap ended paging


def test_callback_schedules_backfill_off_the_request_path_with_jwt_org() -> None:
    import hashlib
    import hmac as _hmac

    secret = "bf_secret"
    s = MagicMock()
    s.shopify_enabled = True
    s.shopify_client_secret = secret
    s.shopify_token_encryption_key = "k"
    s.shopify_api_version = "2024-10"
    user = MagicMock()
    user.id = "u1"
    state = _generate_state(_SHOP, secret, "u1")
    params = {"code": "c", "shop": _SHOP, "state": state}
    sig = _hmac.new(
        secret.encode(),
        "&".join(f"{k}={v}" for k, v in sorted(params.items())).encode(),
        hashlib.sha256,
    ).hexdigest()
    with (
        patch("app.api.shopify_auth.get_settings", return_value=s),
        patch("app.api.shopify_auth.verify_supabase_jwt", AsyncMock(return_value=user)),
        patch("app.api.shopify_auth._get_org_for_user", return_value={"org_id": "org-7"}),
        patch("app.api.shopify_auth._exchange_code", AsyncMock(return_value="shpat_x")),
        patch("app.api.shopify_auth.encrypt_token", return_value="enc"),
        patch("app.api.shopify_auth._upsert_installation_pg"),
        patch("app.api.shopify_auth._register_webhook", AsyncMock(return_value=None)),
        patch("app.api.shopify_auth._backfill_and_drain", AsyncMock()) as bf,
    ):
        resp = TestClient(create_app()).post(
            "/auth/shopify/callback",
            json={**params, "hmac": sig},
            headers={"Authorization": "Bearer t"},
        )
    assert resp.status_code == 200, resp.text
    bf.assert_awaited_once_with("org-7", _SHOP, "shpat_x", "2024-10")
