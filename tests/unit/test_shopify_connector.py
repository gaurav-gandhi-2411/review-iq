"""Unit tests for the Shopify connector: field mapping, HMAC verification, webhook parsing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg2.errors
import pytest
from app.api.webhooks.shopify import (
    _decrypt_token,
    _get_shopify_installation_pg,
    _parse_webhook_payload,
    _verify_shopify_hmac,
    encrypt_token,
)
from app.core.ingestion.shopify_source import (
    ShopifySource,
    _fields_to_dict,
    _node_to_review_row,
    _parse_rating,
)
from fastapi.testclient import TestClient

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
            {
                "key": "product",
                "value": "gid://shopify/Product/1",
                "reference": {"title": product_title},
            },
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
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


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
# Key rotation (audit finding #6) — comma-separated key list
# ---------------------------------------------------------------------------


def test_old_key_still_decrypts_after_rotation() -> None:
    """The core rotation guarantee: a token encrypted BEFORE rotation must still
    decrypt AFTER the key list is updated to "new_key,old_key" -- existing
    installations must not be silently broken by rotating the key."""
    old_key = _make_fernet_key()
    new_key = _make_fernet_key()
    token = "shpat_pre_rotation_token"

    encrypted_before_rotation = encrypt_token(token, old_key)

    rotated_key_list = f"{new_key},{old_key}"
    assert _decrypt_token(encrypted_before_rotation, rotated_key_list) == token


def test_new_encryptions_use_the_first_key_after_rotation() -> None:
    """After rotation, NEW encryptions use the first (new) key -- decrypting with
    only the old key must fail, proving rotation actually took effect going
    forward rather than silently continuing to use the old key."""
    old_key = _make_fernet_key()
    new_key = _make_fernet_key()
    rotated_key_list = f"{new_key},{old_key}"

    encrypted_after_rotation = encrypt_token("shpat_post_rotation_token", rotated_key_list)

    assert _decrypt_token(encrypted_after_rotation, rotated_key_list) == "shpat_post_rotation_token"
    with pytest.raises(ValueError, match="Token decryption failed"):
        _decrypt_token(encrypted_after_rotation, old_key)  # old key alone can't read new ciphertext


def test_single_key_format_unchanged_backward_compatible() -> None:
    """A plain single-key value (today's exact format, no comma) behaves
    identically to before this fix -- zero behavior change for the common case."""
    key = _make_fernet_key()
    token = "shpat_single_key_unchanged"
    assert _decrypt_token(encrypt_token(token, key), key) == token


def test_decrypt_fails_once_old_key_is_fully_removed() -> None:
    """Once an old key is dropped from the list entirely (post-rotation cleanup),
    tokens still encrypted under it can no longer be decrypted -- documents the
    real operational constraint: don't drop the old key until nothing needs it."""
    old_key = _make_fernet_key()
    new_key = _make_fernet_key()
    encrypted_with_old_key = encrypt_token("shpat_orphaned", old_key)

    with pytest.raises(ValueError, match="Token decryption failed"):
        _decrypt_token(encrypted_with_old_key, new_key)  # old key no longer in the list at all


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


# ---------------------------------------------------------------------------
# _get_shopify_installation_pg — BYPASSRLS remediation: two-step lookup
# (resolve_org_for_shopify_shop -> _set_tenant -> the actual row), neither
# step needing review_iq_app to hold BYPASSRLS.
# ---------------------------------------------------------------------------


def _make_conn_cur() -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _make_db_settings() -> MagicMock:
    s = _make_mock_settings()
    s.supabase_database_url = "postgresql://review_iq_app@localhost/postgres"
    return s


def test_get_shopify_installation_pg_no_db_configured_returns_none() -> None:
    with patch(
        "app.api.webhooks.shopify.get_settings",
        return_value=_make_mock_settings(),
    ):
        assert _get_shopify_installation_pg("teststore.myshopify.com") is None


def test_get_shopify_installation_pg_calls_resolve_function_then_set_tenant() -> None:
    conn, cur = _make_conn_cur()
    org_id = "5b6c1e2a-0000-0000-0000-000000000002"
    cur.fetchone.side_effect = [
        (org_id,),  # resolve_org_for_shopify_shop(...)
        ("encrypted-access-token-blob",),  # the actual row
    ]

    with patch("app.api.webhooks.shopify.get_settings", return_value=_make_db_settings()):
        with patch("app.api.webhooks.shopify.psycopg2.connect", return_value=conn):
            with patch("app.api.webhooks.shopify._set_tenant") as mock_set_tenant:
                result = _get_shopify_installation_pg("teststore.myshopify.com")

    assert result == {"org_id": org_id, "access_token_enc": "encrypted-access-token-blob"}
    first_call_sql = cur.execute.call_args_list[0][0][0]
    assert "resolve_org_for_shopify_shop" in first_call_sql
    mock_set_tenant.assert_called_once_with(cur, org_id)


def test_get_shopify_installation_pg_unresolved_shop_returns_none_without_set_tenant() -> None:
    """An unrecognized shop_domain must never reach _set_tenant() -- no org_id to scope
    to, and the whole point is never falling back to a default org."""
    conn, cur = _make_conn_cur()
    cur.fetchone.return_value = (None,)

    with patch("app.api.webhooks.shopify.get_settings", return_value=_make_db_settings()):
        with patch("app.api.webhooks.shopify.psycopg2.connect", return_value=conn):
            with patch("app.api.webhooks.shopify._set_tenant") as mock_set_tenant:
                result = _get_shopify_installation_pg("unknown-store.myshopify.com")

    assert result is None
    mock_set_tenant.assert_not_called()


def test_get_shopify_installation_pg_resolve_function_missing_fails_safe() -> None:
    """Before the accompanying migration's cutover, resolve_org_for_shopify_shop()
    doesn't exist yet -- must drop the webhook (return None), never crash, never fall
    back to the old direct-table-query pattern this refactor removes."""
    conn, cur = _make_conn_cur()
    cur.execute.side_effect = psycopg2.errors.UndefinedFunction("function does not exist")

    with patch("app.api.webhooks.shopify.get_settings", return_value=_make_db_settings()):
        with patch("app.api.webhooks.shopify.psycopg2.connect", return_value=conn):
            result = _get_shopify_installation_pg("teststore.myshopify.com")

    assert result is None


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
                    {
                        "node": _make_node(
                            body="Good bass quality.",
                            rating='{"scale_min":1,"scale_max":5,"value":4}',
                        )
                    },
                    {
                        "node": _make_node(
                            body="", rating='{"scale_min":1,"scale_max":5,"value":1}'
                        )
                    },  # empty → skipped
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
