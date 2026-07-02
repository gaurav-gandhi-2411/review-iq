"""Unit tests for the Google Business Profile connector: field mapping, Pub/Sub
envelope parsing, push-token verification, and GoogleBusinessSource basics."""

from __future__ import annotations

import base64
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.api.webhooks.google import (
    _decrypt_refresh_token,
    _get_google_installation_pg,
    _parse_pubsub_push,
    encrypt_token,
)
from app.core.ingestion.base import SourceError
from app.core.ingestion.google_business_source import (
    GoogleBusinessSource,
    _refresh_access_token,
    _review_to_review_row,
)
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# _review_to_review_row
# ---------------------------------------------------------------------------


def _make_review(
    comment: str = "Great service, highly recommend.",
    star_rating: str = "FIVE",
    display_name: str = "Test Reviewer",
    name: str = "accounts/1/locations/2/reviews/3",
) -> dict:
    return {
        "reviewId": "3",
        "reviewer": {"displayName": display_name},
        "starRating": star_rating,
        "comment": comment,
        "name": name,
    }


def test_review_to_review_row_happy_path() -> None:
    row = _review_to_review_row(_make_review())
    assert row is not None
    assert row["text"] == "Great service, highly recommend."
    assert row["stars"] == 5.0
    assert row["author"] == "Test Reviewer"
    assert row["source_review_id"] == "accounts/1/locations/2/reviews/3"


def test_review_to_review_row_empty_comment_returns_none() -> None:
    row = _review_to_review_row(_make_review(comment=""))
    assert row is None


def test_review_to_review_row_whitespace_comment_returns_none() -> None:
    row = _review_to_review_row(_make_review(comment="   "))
    assert row is None


def test_review_to_review_row_missing_comment_returns_none() -> None:
    review = _make_review()
    del review["comment"]
    assert _review_to_review_row(review) is None


@pytest.mark.parametrize(
    ("star_rating", "expected"),
    [
        ("ONE", 1.0),
        ("TWO", 2.0),
        ("THREE", 3.0),
        ("FOUR", 4.0),
        ("FIVE", 5.0),
        ("STAR_RATING_UNSPECIFIED", None),
        ("SOME_UNKNOWN_VALUE", None),
    ],
)
def test_review_to_review_row_star_rating_mapping(star_rating: str, expected: float | None) -> None:
    row = _review_to_review_row(_make_review(star_rating=star_rating))
    assert row is not None
    assert row.get("stars") == expected


def test_review_to_review_row_with_product() -> None:
    row = _review_to_review_row(_make_review(), product="Widget Pro")
    assert row is not None
    assert row["product"] == "Widget Pro"


def test_review_to_review_row_falls_back_to_review_id() -> None:
    review = _make_review()
    del review["name"]
    row = _review_to_review_row(review)
    assert row is not None
    assert row["source_review_id"] == "3"


def test_review_to_review_row_missing_reviewer_no_author() -> None:
    review = _make_review()
    del review["reviewer"]
    row = _review_to_review_row(review)
    assert row is not None
    assert "author" not in row


# ---------------------------------------------------------------------------
# _parse_pubsub_push
# ---------------------------------------------------------------------------


def _make_pubsub_envelope(data: dict) -> dict:
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return {
        "message": {
            "data": encoded,
            "messageId": "12345",
            "publishTime": "2026-07-02T00:00:00Z",
        },
        "subscription": "projects/test/subscriptions/test-sub",
    }


def test_parse_pubsub_push_valid_message() -> None:
    envelope = _make_pubsub_envelope({"location_name": "accounts/1/locations/2"})
    parsed = _parse_pubsub_push(envelope)
    assert parsed == {"location_name": "accounts/1/locations/2"}


def test_parse_pubsub_push_malformed_base64() -> None:
    envelope = {"message": {"data": "not-valid-base64!!!"}}
    assert _parse_pubsub_push(envelope) is None


def test_parse_pubsub_push_missing_message_key() -> None:
    assert _parse_pubsub_push({"subscription": "x"}) is None


def test_parse_pubsub_push_missing_data_key() -> None:
    assert _parse_pubsub_push({"message": {"messageId": "1"}}) is None


def test_parse_pubsub_push_non_json_data() -> None:
    encoded = base64.b64encode(b"not json").decode()
    envelope = {"message": {"data": encoded}}
    assert _parse_pubsub_push(envelope) is None


def test_parse_pubsub_push_non_dict_json() -> None:
    encoded = base64.b64encode(json.dumps([1, 2, 3]).encode()).decode()
    envelope = {"message": {"data": encoded}}
    assert _parse_pubsub_push(envelope) is None


# ---------------------------------------------------------------------------
# Token encryption
# ---------------------------------------------------------------------------


def _make_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_encrypt_decrypt_refresh_token_roundtrip() -> None:
    key = _make_fernet_key()
    token = "1//0gRefreshTokenExample"
    assert _decrypt_refresh_token(encrypt_token(token, key), key) == token


def test_decrypt_refresh_token_wrong_key_raises_value_error() -> None:
    key_a, key_b = _make_fernet_key(), _make_fernet_key()
    encrypted = encrypt_token("refresh_abc", key_a)
    with pytest.raises(ValueError, match="Token decryption failed"):
        _decrypt_refresh_token(encrypted, key_b)


def test_decrypt_refresh_token_tampered_ciphertext_raises_value_error() -> None:
    key = _make_fernet_key()
    encrypted = encrypt_token("refresh_abc", key)
    tampered = encrypted[:-4] + "XXXX"
    with pytest.raises(ValueError, match="Token decryption failed"):
        _decrypt_refresh_token(tampered, key)


# ---------------------------------------------------------------------------
# Push-token verification (endpoint-level)
# ---------------------------------------------------------------------------


def _make_mock_settings(push_token: str = "") -> MagicMock:
    s = MagicMock()
    s.google_pubsub_push_token = push_token
    s.google_token_encryption_key = ""
    s.supabase_database_url = ""
    return s


def test_webhook_rejects_missing_token() -> None:
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    envelope = _make_pubsub_envelope({"location_name": "accounts/1/locations/2"})

    with patch(
        "app.api.webhooks.google.get_settings",
        return_value=_make_mock_settings(push_token="real_secret_token"),
    ):
        resp = client.post("/webhooks/google/reviews", json=envelope)
    assert resp.status_code == 401


def test_webhook_rejects_wrong_token() -> None:
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    envelope = _make_pubsub_envelope({"location_name": "accounts/1/locations/2"})

    with patch(
        "app.api.webhooks.google.get_settings",
        return_value=_make_mock_settings(push_token="real_secret_token"),
    ):
        resp = client.post(
            "/webhooks/google/reviews", json=envelope, params={"token": "wrong_token"}
        )
    assert resp.status_code == 401


def test_webhook_accepts_correct_token_returns_200() -> None:
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    envelope = _make_pubsub_envelope({"location_name": "accounts/1/locations/2"})

    with patch(
        "app.api.webhooks.google.get_settings",
        return_value=_make_mock_settings(push_token="real_secret_token"),
    ):
        resp = client.post(
            "/webhooks/google/reviews", json=envelope, params={"token": "real_secret_token"}
        )
    assert resp.status_code == 200


def test_webhook_no_push_token_configured_returns_503() -> None:
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    envelope = _make_pubsub_envelope({"location_name": "accounts/1/locations/2"})

    with patch(
        "app.api.webhooks.google.get_settings",
        return_value=_make_mock_settings(push_token=""),
    ):
        resp = client.post(
            "/webhooks/google/reviews", json=envelope, params={"token": "anything"}
        )
    assert resp.status_code == 503


def test_compare_digest_timing_safe_used_directly() -> None:
    """Sanity check that hmac.compare_digest behaves as expected for equal/unequal tokens."""
    assert hmac.compare_digest("abc", "abc") is True
    assert hmac.compare_digest("abc", "xyz") is False


# ---------------------------------------------------------------------------
# _get_google_installation_pg — no DB configured returns None
# ---------------------------------------------------------------------------


def test_get_google_installation_pg_no_db_configured_returns_none() -> None:
    with patch(
        "app.api.webhooks.google.get_settings",
        return_value=_make_mock_settings(),
    ):
        assert _get_google_installation_pg("accounts/1/locations/2") is None


# ---------------------------------------------------------------------------
# GoogleBusinessSource — source_type / source_meta / mocked httpx fetch
# ---------------------------------------------------------------------------


def test_google_business_source_type() -> None:
    source = GoogleBusinessSource(
        location_name="accounts/1/locations/2",
        account_name="accounts/1",
        refresh_token="refresh_abc",
        client_id="client_id",
        client_secret="client_secret",
    )
    assert source.source_type == "google_business"


def test_google_business_source_meta_before_fetch() -> None:
    source = GoogleBusinessSource(
        location_name="accounts/1/locations/2",
        account_name="accounts/1",
        refresh_token="refresh_abc",
        client_id="client_id",
        client_secret="client_secret",
    )
    meta = source.source_meta()
    assert meta["location_name"] == "accounts/1/locations/2"
    assert meta["account_name"] == "accounts/1"
    assert meta["fetched_count"] == 0


@pytest.mark.asyncio
async def test_refresh_access_token_success() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"access_token": "new_access_token", "expires_in": 3600})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        token = await _refresh_access_token("refresh_abc", "client_id", "client_secret")

    assert token == "new_access_token"


@pytest.mark.asyncio
async def test_refresh_access_token_no_token_raises_source_error() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        with pytest.raises(SourceError, match="no access_token"):
            await _refresh_access_token("refresh_abc", "client_id", "client_secret")


@pytest.mark.asyncio
async def test_google_business_source_fetch_single_page() -> None:
    """fetch_reviews returns ReviewRows from a single-page Reviews response."""
    token_resp = MagicMock()
    token_resp.raise_for_status = MagicMock()
    token_resp.json = MagicMock(return_value={"access_token": "access_abc", "expires_in": 3600})

    reviews_resp = MagicMock()
    reviews_resp.raise_for_status = MagicMock()
    reviews_resp.json = MagicMock(
        return_value={
            "reviews": [
                _make_review(comment="Solid product."),
                _make_review(comment=""),  # empty comment → skipped
            ]
        }
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=token_resp)
        mock_ctx.get = AsyncMock(return_value=reviews_resp)
        mock_client_cls.return_value = mock_ctx

        source = GoogleBusinessSource(
            location_name="accounts/1/locations/2",
            account_name="accounts/1",
            refresh_token="refresh_abc",
            client_id="client_id",
            client_secret="client_secret",
        )
        rows = await source.fetch_reviews()

    assert len(rows) == 1
    assert rows[0]["text"] == "Solid product."
    assert source.source_meta()["fetched_count"] == 1


@pytest.mark.asyncio
async def test_google_business_source_pagination() -> None:
    token_resp = MagicMock()
    token_resp.raise_for_status = MagicMock()
    token_resp.json = MagicMock(return_value={"access_token": "access_abc", "expires_in": 3600})

    page1 = MagicMock()
    page1.raise_for_status = MagicMock()
    page1.json = MagicMock(
        return_value={"reviews": [_make_review(comment="Page one review.")], "nextPageToken": "cursor1"}
    )
    page2 = MagicMock()
    page2.raise_for_status = MagicMock()
    page2.json = MagicMock(return_value={"reviews": [_make_review(comment="Page two review.")]})

    call_count = 0

    async def fake_get(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return page1 if call_count == 1 else page2

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=token_resp)
        mock_ctx.get = fake_get
        mock_client_cls.return_value = mock_ctx

        source = GoogleBusinessSource(
            location_name="accounts/1/locations/2",
            account_name="accounts/1",
            refresh_token="refresh_abc",
            client_id="client_id",
            client_secret="client_secret",
        )
        rows = await source.fetch_reviews()

    assert len(rows) == 2
    assert rows[0]["text"] == "Page one review."
    assert rows[1]["text"] == "Page two review."
    assert call_count == 2


@pytest.mark.asyncio
async def test_google_business_source_raises_on_http_error() -> None:
    import httpx

    token_resp = MagicMock()
    token_resp.raise_for_status = MagicMock()
    token_resp.json = MagicMock(return_value={"access_token": "access_abc", "expires_in": 3600})

    async def fake_get(*args: object, **kwargs: object) -> MagicMock:
        request = httpx.Request("GET", "https://mybusiness.googleapis.com/v4/x/reviews")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=token_resp)
        mock_ctx.get = fake_get
        mock_client_cls.return_value = mock_ctx

        source = GoogleBusinessSource(
            location_name="accounts/1/locations/2",
            account_name="accounts/1",
            refresh_token="refresh_abc",
            client_id="client_id",
            client_secret="client_secret",
        )
        with pytest.raises(SourceError, match="Google Reviews HTTP"):
            await source.fetch_reviews()
