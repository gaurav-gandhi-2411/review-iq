"""Google Business Profile (GBP) review connector — polls the Business Profile Reviews API.

ACCESS STATUS (2026): Built but INACTIVE — awaiting Google's manual access approval.
  Google's Business Profile Performance/Reviews APIs are NOT self-serve. New GCP
  projects start at zero quota; Google requires a verified GBP location that has
  been active 60+ days and has a business website before it will approve an access
  request. This connector is fully implemented against the documented API surface
  but cannot be exercised against live data until that approval lands.

WHAT GG MUST SET UP (escalation items):
  - A GCP project + OAuth 2.0 client (Web application type) → GOOGLE_CLIENT_ID +
    GOOGLE_CLIENT_SECRET.
  - Submit the Business Profile API access request at
    https://developers.google.com/my-business/content/prereqs (requires a verified,
    60+ day old GBP location with a linked business website).
  - A Cloud Pub/Sub topic (GOOGLE_PUBSUB_TOPIC) with the Google-managed service
    account `mybusiness-api-pubsub@system.gserviceaccount.com` granted the
    `pubsub.topics.publish` IAM role on that topic.
  - A push subscription on that topic pointed at
    `https://<api-domain>/webhooks/google/reviews?token=<GOOGLE_PUBSUB_PUSH_TOKEN>`.

TOKEN LIFECYCLE (deliberate delta from the Shopify connector):
  Google OAuth access_token expires in ~1 hour. The long-lived credential we persist
  is refresh_token (Fernet-encrypted, in google_business_installations.refresh_token_enc).
  Every API call — connector fetch AND webhook processing — must first exchange the
  refresh_token for a fresh access_token via _refresh_access_token(). Never store or
  reuse an access_token across calls.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.core.ingestion.base import ReviewRow, SourceError

log = structlog.get_logger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Google's star rating enum → numeric scale. STAR_RATING_UNSPECIFIED (and any
# unrecognised value) maps to None rather than guessing a default.
_STAR_RATING_MAP: dict[str, float] = {
    "ONE": 1.0,
    "TWO": 2.0,
    "THREE": 3.0,
    "FOUR": 4.0,
    "FIVE": 5.0,
}


async def _refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    """Exchange a long-lived refresh_token for a short-lived access_token.

    Raises SourceError on HTTP or network failure so callers can distinguish a
    token-refresh failure from an empty result set.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(
                f"Google token refresh HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise SourceError(f"Google token refresh request failed: {exc}") from exc

    data = resp.json()
    access_token = data.get("access_token", "")
    if not access_token:
        raise SourceError(f"Google token refresh returned no access_token: {data}")
    return str(access_token)


def _review_to_review_row(review: dict[str, Any], product: str | None = None) -> ReviewRow | None:
    """Map a GBP Review resource to a ReviewRow. Returns None if comment is empty/missing.

    Review resource fields (per Business Profile API):
      reviewId, reviewer.displayName, starRating (enum string), comment,
      name (full resource path, e.g. "accounts/x/locations/y/reviews/z").
    """
    comment = review.get("comment") or ""
    if not comment.strip():
        return None

    row: ReviewRow = {"text": comment}

    if product:
        row["product"] = product

    star_rating = review.get("starRating")
    if star_rating in _STAR_RATING_MAP:
        row["stars"] = _STAR_RATING_MAP[star_rating]

    reviewer = review.get("reviewer") or {}
    display_name = reviewer.get("displayName")
    if display_name:
        row["author"] = str(display_name)

    # Full resource name is the stable dedup key; falls back to reviewId if absent.
    name = review.get("name") or review.get("reviewId")
    if name:
        row["source_review_id"] = str(name)

    return row


class GoogleBusinessSource:
    """Google Business Profile review ingestion via the Business Profile Reviews API.

    Uses pageToken-based pagination. Refreshes an access_token from the stored
    refresh_token on every fetch_reviews() call (Google access tokens expire in ~1h,
    so no access_token is ever persisted).

    Args:
        location_name: e.g. "accounts/{account_id}/locations/{location_id}".
        account_name: e.g. "accounts/{account_id}".
        refresh_token: Long-lived OAuth refresh token obtained during app installation.
        client_id: Google OAuth client ID.
        client_secret: Google OAuth client secret.
    """

    def __init__(
        self,
        location_name: str,
        account_name: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._location_name = location_name
        self._account_name = account_name
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._fetched_count = 0

    @property
    def source_type(self) -> str:
        return "google_business"

    async def fetch_reviews(self) -> list[ReviewRow]:
        """Fetch all reviews for this location via paginated GET requests.

        Raises SourceError on HTTP or network failure so callers can distinguish a
        retrieval failure from empty results.
        """
        access_token = await _refresh_access_token(
            self._refresh_token, self._client_id, self._client_secret
        )

        rows: list[ReviewRow] = []
        page_token: str | None = None
        url = f"https://mybusiness.googleapis.com/v4/{self._location_name}/reviews"

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict[str, str] = {"pageSize": "50"}
                if page_token:
                    params["pageToken"] = page_token

                try:
                    resp = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=params,
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(
                        f"Google Reviews HTTP {exc.response.status_code} "
                        f"for {self._location_name}"
                    ) from exc
                except httpx.RequestError as exc:
                    raise SourceError(
                        f"Google Reviews request failed for {self._location_name}: {exc}"
                    ) from exc

                payload = resp.json()
                reviews = payload.get("reviews", [])

                for review in reviews:
                    row = _review_to_review_row(review)
                    if row is not None:
                        rows.append(row)

                log.debug(
                    "google_business.page_fetched",
                    location=self._location_name,
                    page_count=len(reviews),
                    running_total=len(rows),
                )

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        self._fetched_count = len(rows)
        log.info(
            "google_business.fetch_complete",
            location=self._location_name,
            total=self._fetched_count,
        )
        return rows

    def source_meta(self) -> dict[str, object]:
        return {
            "location_name": self._location_name,
            "account_name": self._account_name,
            "fetched_count": self._fetched_count,
        }
