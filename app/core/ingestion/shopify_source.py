"""Shopify review connector — polls the Standard Product Review Metaobject via GraphQL.

RESEARCH FINDING (2026):
  Shopify's native Product Reviews app was shut down May 2024. There is NO native
  review API or REST endpoint. Reviews now live in the Standard Product Review
  Metaobject, written by participating review apps (Judge.me, Loox, Yotpo, etc.)
  that are approved for the Standard Product Review Syndication Program.

  This connector reads from that metaobject. It requires:
    1. The seller's store has a review app (e.g. Judge.me free tier) that participates
       in the Syndication Program and writes reviews to the metaobject.
    2. The review-iq Shopify app is installed on the seller's store with OAuth scopes:
         write_product_reviews, read_metaobjects, read_products, read_customers
    3. The seller's OAuth access_token is stored (per-org) and passed here.

  Real-time webhooks use topic METAOBJECTS_CREATE filtered by type:product_review.
  That endpoint lives in app/api/webhooks/shopify.py and requires a deployed public URL.

WHAT GG MUST SET UP (escalation items):
  - Shopify Partner account at partners.shopify.com
  - Create a Shopify app → get SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET
  - Configure OAuth redirect URL (https://<your-api-domain>/auth/shopify/callback)
  - Configure webhook URL (https://<your-api-domain>/webhooks/shopify/reviews)
  - Required OAuth scopes: write_product_reviews read_metaobjects read_products
  - Development store + a free review app (e.g. Judge.me) to test with real review data
  - Optionally apply for the Standard Product Review Syndication Program if Shopify
    requires it for reading (may only be required for apps WRITING to the metaobject)
"""

from __future__ import annotations

import json
import structlog
from typing import Any

import httpx

from app.core.ingestion.base import ReviewRow, SourceError

log = structlog.get_logger(__name__)

# GraphQL Admin API query — fetches product_review metaobjects with product title
# resolved inline (avoids a second round-trip per review).
_REVIEW_QUERY = """
query GetProductReviews($after: String) {
  metaobjects(type: "product_review", first: 50, after: $after, sortKey: UPDATED_AT) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        fields {
          key
          value
          reference {
            ... on Product {
              title
            }
          }
        }
      }
    }
  }
}
"""


def _parse_rating(value: str | None) -> float | None:
    """Parse Shopify Rating JSON → star value (1–5). Returns None on any failure."""
    if not value:
        return None
    try:
        data = json.loads(value)
        return float(data["value"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _fields_to_dict(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten metaobject fields array into {key: value|resolved_title} dict."""
    result: dict[str, Any] = {}
    for f in fields:
        key = f.get("key", "")
        # For reference fields (e.g. product), prefer the resolved title over the GID.
        ref = f.get("reference") or {}
        if ref.get("title"):
            result[key] = ref["title"]
        else:
            result[key] = f.get("value")
    return result


def _node_to_review_row(node: dict[str, Any]) -> ReviewRow | None:
    """Map a single metaobject node to a ReviewRow. Returns None if no body text."""
    fields = _fields_to_dict(node.get("fields", []))
    body = fields.get("body") or ""
    if not body.strip():
        return None

    row: ReviewRow = {"text": body}

    product = fields.get("product")
    if product:
        row["product"] = str(product)

    rating = _parse_rating(fields.get("rating"))
    if rating is not None:
        row["stars"] = rating

    author = fields.get("author_display_name")
    if author:
        row["author"] = str(author)

    lang = fields.get("language")
    if lang:
        row["language"] = str(lang)

    # Use the Shopify metaobject GID as the stable dedup key.
    gid = node.get("id")
    if gid:
        row["source_review_id"] = str(gid)

    return row


class ShopifySource:
    """Shopify product review ingestion via the Standard Product Review Metaobject.

    Uses GraphQL Admin API with cursor-based pagination. Fetches all available
    product_review metaobjects in pages of 50.

    Args:
        shop_domain: e.g. "mystore.myshopify.com" — no https:// prefix.
        access_token: Per-store OAuth access token obtained during app installation.
        api_version: Shopify API version (minimum 2024-01 for metaobject webhooks).
    """

    def __init__(
        self,
        shop_domain: str,
        access_token: str,
        api_version: str = "2024-10",
    ) -> None:
        self._shop_domain = shop_domain
        self._access_token = access_token
        self._api_version = api_version
        self._url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self._fetched_count = 0

    @property
    def source_type(self) -> str:
        return "shopify"

    async def fetch_reviews(self) -> list[ReviewRow]:
        """Fetch all product_review metaobjects via paginated GraphQL queries.

        Raises SourceError on HTTP or GraphQL-level failure so callers can
        distinguish a retrieval failure from empty results.
        """
        rows: list[ReviewRow] = []
        cursor: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                variables: dict[str, Any] = {}
                if cursor:
                    variables["after"] = cursor

                try:
                    resp = await client.post(
                        self._url,
                        headers={
                            "X-Shopify-Access-Token": self._access_token,
                            "Content-Type": "application/json",
                        },
                        json={"query": _REVIEW_QUERY, "variables": variables},
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(
                        f"Shopify GraphQL HTTP {exc.response.status_code} "
                        f"for {self._shop_domain}"
                    ) from exc
                except httpx.RequestError as exc:
                    raise SourceError(
                        f"Shopify GraphQL request failed for {self._shop_domain}: {exc}"
                    ) from exc

                payload = resp.json()
                errors = payload.get("errors")
                if errors:
                    raise SourceError(
                        f"Shopify GraphQL errors for {self._shop_domain}: {errors}"
                    )

                data = (payload.get("data") or {}).get("metaobjects", {})
                edges = data.get("edges", [])
                page_info = data.get("pageInfo", {})

                for edge in edges:
                    node = edge.get("node", {})
                    row = _node_to_review_row(node)
                    if row is not None:
                        rows.append(row)

                log.debug(
                    "shopify.page_fetched",
                    shop=self._shop_domain,
                    page_count=len(edges),
                    running_total=len(rows),
                )

                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")

        self._fetched_count = len(rows)
        log.info(
            "shopify.fetch_complete",
            shop=self._shop_domain,
            total=self._fetched_count,
        )
        return rows

    def source_meta(self) -> dict[str, object]:
        return {
            "shop_domain": self._shop_domain,
            "api_version": self._api_version,
            "fetched_count": self._fetched_count,
        }
