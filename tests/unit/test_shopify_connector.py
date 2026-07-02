"""Unit tests for the Shopify connector: field mapping, HMAC verification, webhook parsing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.ingestion.shopify_source import (
    ShopifySource,
    _fields_to_dict,
    _node_to_review_row,
    _parse_rating,
)
from app.api.webhooks.shopify import (
    _decrypt_token,
    _parse_webhook_payload,
    _verify_shopify_hmac,
    encrypt_token,
)


# ---------------------------------------------------------------------------
# Field parsing helpers
# ---------------------------------------------------------------------------


def test_parse_rating_valid() -> None:
    assert _parse_rating('{"scale_min": 1, "scale_max": 5, "value": 4}') == 4.0


def test_parse_rating_float() -> None:
    assert _parse_rating('{"scale_min": 1, "scale_max": 5, "value": 3.5}') == 3.5


def test_parse_rating_none_input() -> None:
    assert _parse_rating(None) is None


def test_parse_rating_empty() -> None:
    assert _parse_rating("") is None


def test_parse_rating_invalid_json() -> None:
    assert _parse_rating("not-json") is None


def test_fields_to_dict_basic() -> None:
    fields = [
        {"key": "body", "value": "Great product!"},
        {"key": "rating", "value": '{"scale_min":1,"scale_max":5,"value":5}'},
        {"key": "author_display_name", "value": "Gaurav D."},
    ]
    result = _fields_to_dict(fields)
    assert result["body"] == "Great product!"
    assert result["author_display_name"] == "Gaurav D."


def test_fields_to_dict_reference_prefers_title() -> None:
    """Product reference fields should resolve to the product title, not the GID."""
    fields = [
        {
            "key": "product",
            "value": "gid://shopify/Product/123",
            "reference": {"title": "Boat Rockerz 450"},
        }
    ]
    result = _fields_to_dict(fields)
    assert result["product"] == "Boat Rockerz 450"


def test_fields_to_dict_no_reference_falls_back_to_value() -> None:
    fields = [{"key": "product", "value": "gid://shopify/Product/123", "reference": {}}]
    result = _fields_to_dict(fields)
    assert result["product"] == "gid://shopify/Product/123"


# ---------------------------------------------------------------------------
# Node → ReviewRow mapping
# ---------------------------------------------------------------------------


def _make_node(
    body: str = "Great headphones, love the bass.",
    rating: str = '{"scale_min":1,"scale_max":5,"value":5}',
    product_title: str = "Boat Rockerz 450",
    author: str = "Test User",
    language: str = "en",
    gid: str = "gid://shopify/Metaobject/99",
) -> dict:
    return {
        "id": gid,
        "fields": [
            {"key": "body", "value": body},
            {"key": "rating", "value": rating},
            {"key": "product", "value": "gid://shopify/Product/1", "reference": {"title": product_title}},
            {"key": "author_display_name", "value": author},
            {"key": "language", "value": language},
        ],
    }


def test_node_to_review_row_happy_path() -> None:
    row = _node_to_review_row(_make_node())
    assert row is not None
    assert row["text"] == "Great headphones, love the bass."
    assert row["stars"] == 5.0
    assert row["product"] == "Boat Rockerz 450"
    assert row["author"] == "Test User"
    assert row["language"] == "en"
    assert row["source_review_id"] == "gid://shopify/Metaobject/99"


def test_node_to_review_row_no_body_returns_none() -> None:
    node = _make_node(body="")
    assert _node_to_review_row(node) is None


def test_node_to_review_row_whitespace_body_returns_none() -> None:
    node = _make_node(body="   ")
    assert _node_to_review_row(node) is None


def test_node_to_review_row_missing_optional_fields() -> None:
    node = {"id": "gid://shopify/Metaobject/1", "fields": [{"key": "body", "value": "Good."}]}
    row = _node_to_review_row(node)
    assert row is not None
    assert row["text"] == "Good."
    assert "stars" not in row
    assert "product" not in row
    assert "author" not in row


# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------


def _make_hmac(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


def test_verify_shopify_hmac_correct() -> None:
    body = b'{"type":"product_review"}'
    secret = "test_secret_abc"
    sig = _make_hmac(secret, body)
    assert _verify_shopify_hmac(body, sig, secret) is True


def test_verify_shopify_hmac_wrong_secret() -> None:
    body = b'{"type":"product_review"}'
    sig = _make_hmac("real_secret", body)
    assert _verify_shopify_hmac(body, sig, "wrong_secret") is False


def test_verify_shopify_hmac_tampered_body() -> None:
    body = b'{"type":"product_review"}'
    sig = _make_hmac("secret", body)
    tampered = b'{"type":"product_review","extra":"injected"}'
    assert _verify_shopify_hmac(tampered, sig, "secret") is False


def test_verify_shopify_hmac_empty_body() -> None:
    sig = _make_hmac("secret", b"")
    assert _verify_shopify_hmac(b"", sig, "secret") is True


# ---------------------------------------------------------------------------
# Token encryption
# ---------------------------------------------------------------------------


def _make_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def test_encrypt_decrypt_roundtrip() -> None:
    key = _make_fernet_key()
    token = "shpat_test_placeholder_not_a_real_token_value"
    assert _decrypt_token(encrypt_token(token, key), key) == token


def test_decrypt_wrong_key_raises_value_error() -> None:
    key_a, key_b = _make_fernet_key(), _make_fernet_key()
    encrypted = encrypt_token("shpat_abc", key_a)
    with pytest.raises(ValueError, match="Token decryption failed"):
        _decrypt_token(encrypted, key_b)


def test_decrypt_tampered_ciphertext_raises_value_error() -> None:
    key = _make_fernet_key()
    encrypted = encrypt_token("shpat_abc", key)
    tampered = encrypted[:-4] + "XXXX"
    with pytest.raises(ValueError, match="Token decryption failed"):
        _decrypt_token(tampered, key)


# ---------------------------------------------------------------------------
# Webhook payload parsing
# ---------------------------------------------------------------------------


def test_parse_webhook_payload_product_review() -> None:
    payload = {
        "type": "product_review",
        "admin_graphql_api_id": "gid://shopify/Metaobject/42",
        "fields": [
            {"key": "body", "value": "Excellent quality!"},
            {"key": "rating", "value": '{"scale_min":1,"scale_max":5,"value":5}'},
        ],
    }
    node = _parse_webhook_payload(payload)
    assert node is not None
    assert node["id"] == "gid://shopify/Metaobject/42"


def test_parse_webhook_payload_non_review_returns_none() -> None:
    payload = {"type": "some_other_metaobject", "fields": []}
    assert _parse_webhook_payload(payload) is None


def test_parse_webhook_payload_integer_id_fallback() -> None:
    """REST API may deliver numeric id instead of admin_graphql_api_id."""
    payload = {"type": "product_review", "id": 12345, "fields": []}
    node = _parse_webhook_payload(payload)
    assert node is not None
    assert node["id"] == "12345"


# ---------------------------------------------------------------------------
# Webhook endpoint — HMAC gate (no real DB / LLM)
# ---------------------------------------------------------------------------


def _make_signed_request(client: TestClient, body: bytes, secret: str) -> object:
    sig = _make_hmac(secret, body)
    return client.post(
        "/webhooks/shopify/reviews",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": sig,
            "X-Shopify-Shop-Domain": "teststore.myshopify.com",
            "X-Shopify-Topic": "metaobjects/create",
            "Content-Type": "application/json",
        },
    )


def _make_mock_settings(client_secret: str = "") -> MagicMock:
    """Return a mock Settings object with Shopify fields set."""
    s = MagicMock()
    s.shopify_client_secret = client_secret
    s.shopify_token_encryption_key = ""
    s.supabase_database_url = ""
    return s


def test_webhook_rejects_bad_hmac() -> None:
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    body = json.dumps({"type": "product_review", "fields": []}).encode()
    bad_sig = _make_hmac("wrong_secret", body)

    with patch(
        "app.api.webhooks.shopify.get_settings",
        return_value=_make_mock_settings(client_secret="real_secret"),
    ):
        resp = client.post(
            "/webhooks/shopify/reviews",
            content=body,
            headers={
                "X-Shopify-Hmac-Sha256": bad_sig,
                "X-Shopify-Shop-Domain": "test.myshopify.com",
                "X-Shopify-Topic": "metaobjects/create",
            },
        )
    assert resp.status_code == 401


def test_webhook_accepts_valid_hmac_returns_200() -> None:
    from app.main import create_app

    secret = "shopify_test_secret"
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    body = json.dumps(
        {
            "type": "product_review",
            "admin_graphql_api_id": "gid://shopify/Metaobject/1",
            "fields": [{"key": "body", "value": "Test review"}],
        }
    ).encode()

    with patch(
        "app.api.webhooks.shopify.get_settings",
        return_value=_make_mock_settings(client_secret=secret),
    ):
        resp = _make_signed_request(client, body, secret)
    assert resp.status_code == 200


def test_webhook_no_client_secret_returns_503() -> None:
    from app.main import create_app

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    with patch(
        "app.api.webhooks.shopify.get_settings",
        return_value=_make_mock_settings(client_secret=""),
    ):
        resp = client.post(
            "/webhooks/shopify/reviews",
            content=b"{}",
            headers={
                "X-Shopify-Hmac-Sha256": "anything",
                "X-Shopify-Shop-Domain": "test.myshopify.com",
                "X-Shopify-Topic": "metaobjects/create",
            },
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# ShopifySource.fetch_reviews — mock httpx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shopify_source_fetch_single_page() -> None:
    """fetch_reviews returns ReviewRows from a single-page GraphQL response."""
    mock_response = {
        "data": {
            "metaobjects": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {"node": _make_node(body="Good bass quality.", rating='{"scale_min":1,"scale_max":5,"value":4}')},
                    {"node": _make_node(body="", rating='{"scale_min":1,"scale_max":5,"value":1}')},  # empty → skipped
                ],
            }
        }
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        source = ShopifySource(
            shop_domain="teststore.myshopify.com",
            access_token="shpat_test",
        )
        rows = await source.fetch_reviews()

    assert len(rows) == 1  # empty-body row is skipped
    assert rows[0]["text"] == "Good bass quality."
    assert rows[0]["stars"] == 4.0
    assert source.source_meta()["fetched_count"] == 1


@pytest.mark.asyncio
async def test_shopify_source_pagination() -> None:
    """fetch_reviews follows cursors across multiple pages."""
    page1 = {
        "data": {
            "metaobjects": {
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor_abc"},
                "edges": [{"node": _make_node(body="Page one review.")}],
            }
        }
    }
    page2 = {
        "data": {
            "metaobjects": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [{"node": _make_node(body="Page two review.")}],
            }
        }
    }

    call_count = 0

    async def fake_post(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=page1 if call_count == 1 else page2)
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = fake_post
        mock_client_cls.return_value = mock_ctx

        source = ShopifySource("teststore.myshopify.com", "shpat_test")
        rows = await source.fetch_reviews()

    assert len(rows) == 2
    assert rows[0]["text"] == "Page one review."
    assert rows[1]["text"] == "Page two review."
    assert call_count == 2


@pytest.mark.asyncio
async def test_shopify_source_raises_on_graphql_errors() -> None:
    from app.core.ingestion.base import SourceError

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"errors": [{"message": "Access denied"}]})

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_ctx.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_ctx

        source = ShopifySource("teststore.myshopify.com", "shpat_test")
        with pytest.raises(SourceError, match="GraphQL errors"):
            await source.fetch_reviews()
