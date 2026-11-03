"""Post-install Shopify backfill: existing reviews -> the durable ingest queue.

Runs once after a successful OAuth install (and again, harmlessly, on re-install). It does NOT
call the LLM itself: it stages rows in batch_job_rows exactly like POST /v2/ingest/csv does, and
the existing drain worker (app/core/ingest_worker.py, with its bulk-call rate limiter) extracts
them. Cost is therefore bounded three ways: a hard row cap, dedupe against already-extracted
text, and the same Groq limiter every other bulk path uses.

Dedupe is by ReviewRequest.input_hash() (text only), the same key the extraction cache uses, so
a row skipped here is a row the cache would have served for free anyway.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

import structlog

from app.core.csv_ingest import MAX_ROWS
from app.core.ingestion.base import ReviewRow, SourceError
from app.core.ingestion.shopify_source import ShopifySource
from app.core.schemas import ReviewRequest
from app.core.storage_pg import (
    create_batch_job_pg,
    enqueue_batch_job_rows_pg,
    existing_input_hashes_pg,
    get_org_retention_pg,
    update_batch_job_pg,
)

log = structlog.get_logger(__name__)

# Same ceiling as the CSV free-tier cap: ~500 extractions is roughly $0.27 at the measured
# $0.000534 blended cost (eval/results/token_cost_measurement_n106.json), and it keeps one
# install from monopolising the 200K-token/day Groq pool.
BACKFILL_ROW_CAP = MAX_ROWS


@dataclass(frozen=True)
class BackfillPlan:
    rows: list[ReviewRow]
    fetched: int
    skipped_duplicate: int


def plan_rows(rows: list[ReviewRow], already_extracted: set[str]) -> BackfillPlan:
    """Drop in-batch duplicates and rows whose text hash is already extracted. Pure."""
    seen: set[str] = set()
    keep: list[ReviewRow] = []
    for row in rows:
        h = ReviewRequest(text=row["text"]).input_hash()
        if h in already_extracted or h in seen:
            continue
        seen.add(h)
        keep.append(row)
    return BackfillPlan(rows=keep, fetched=len(rows), skipped_duplicate=len(rows) - len(keep))


async def enqueue_backfill(
    org_id: str, shop_domain: str, access_token: str, api_version: str
) -> str | None:
    """Fetch, dedupe and stage a shop's existing reviews. Returns the job_id, or None.

    Never raises: a backfill failure must not undo a completed install (the merchant is
    connected and live webhooks still work). Failures are logged with the shop and org.
    """
    try:
        retention_mode, _ = await asyncio.to_thread(get_org_retention_pg, org_id)
        if retention_mode != "retained":
            # Same constraint as CSV ingest (ADR 0024/0025): staged rows are durable by design.
            log.info("shopify_backfill.skipped_stateless", org_id=org_id, shop=shop_domain)
            return None

        source = ShopifySource(shop_domain, access_token, api_version)
        rows = await source.fetch_reviews(limit=BACKFILL_ROW_CAP)
        hashes = [ReviewRequest(text=r["text"]).input_hash() for r in rows]
        existing = await asyncio.to_thread(existing_input_hashes_pg, org_id, hashes)
        plan = plan_rows(rows, existing)
        if not plan.rows:
            log.info(
                "shopify_backfill.nothing_to_enqueue",
                org_id=org_id,
                shop=shop_domain,
                fetched=plan.fetched,
            )
            return None

        job_id = str(uuid.uuid4())
        meta = json.dumps({"source": "shopify", "shop_domain": shop_domain, "input_hashes": []})
        await asyncio.to_thread(create_batch_job_pg, org_id, job_id, len(plan.rows), meta)
        await asyncio.to_thread(
            enqueue_batch_job_rows_pg,
            org_id,
            job_id,
            [r["text"] for r in plan.rows],
            [r.get("product") for r in plan.rows],
        )
        await asyncio.to_thread(update_batch_job_pg, org_id, job_id, status="processing")
        log.info(
            "shopify_backfill.enqueued",
            org_id=org_id,
            shop=shop_domain,
            job_id=job_id,
            enqueued=len(plan.rows),
            fetched=plan.fetched,
            skipped_duplicate=plan.skipped_duplicate,
        )
        return job_id
    except SourceError as exc:
        log.error("shopify_backfill.fetch_failed", org_id=org_id, shop=shop_domain, error=str(exc))
    except Exception as exc:  # noqa: BLE001 -- install already succeeded; never propagate
        log.error("shopify_backfill.failed", org_id=org_id, shop=shop_domain, error=str(exc))
    return None
