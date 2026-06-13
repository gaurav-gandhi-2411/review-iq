"""Unit tests for authenticity endpoint pre-LLM DB cache (token-conservation).

Asserts:
- When get_authenticity_audit_by_hash_pg returns a row, engine.score_single
  is NOT called (no LLM re-spend).
- The cached response has the correct shape (score, label, flags).
- When no cached audit exists, engine.score_single IS called once and the
  result is saved via save_authenticity_audit_pg.
- _review_hash produces consistent sha256 digests matching the engine's own hash.
- _audit_row_to_result handles unknown flags gracefully.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.auth.api_key import ApiKeyContext, require_api_key
from app.core.authenticity.schema import AuthenticityFlag, AuthenticityLabel, AuthenticityResult
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_ORG_ID = str(uuid.uuid4())
_KEY_ID = str(uuid.uuid4())
_USAGE_ID = str(uuid.uuid4())

_CTX = ApiKeyContext(
    org_id=_ORG_ID,
    api_key_id=_KEY_ID,
    key_name="test-key",
    usage_record_id=_USAGE_ID,
)

_REVIEW_TEXT = "This product is the best I have ever used, highly recommend!"
_REVIEW_HASH = hashlib.sha256(_REVIEW_TEXT.encode()).hexdigest()


def _fake_engine_result(score: float = 0.82) -> AuthenticityResult:
    return AuthenticityResult(
        score=score,
        label=AuthenticityLabel.GENUINE,
        flags=[],
        reasons="looks genuine",
        review_hash=_REVIEW_HASH,
        scored_at=datetime.now(UTC),
        model_used="test-model",
    )


def _fake_audit_row(
    score: float = 0.75,
    label: str = "genuine",
    flags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "score": score,
        "label": label,
        "flags": flags or [],
        "review_hash": _REVIEW_HASH,
    }


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    app.dependency_overrides[require_api_key] = lambda: _CTX
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Pre-LLM short-circuit: cache hit
# ---------------------------------------------------------------------------


def test_authenticity_cache_hit_skips_engine(client: TestClient) -> None:
    """When an audit row exists, engine.score_single must NOT be called."""
    audit_row = _fake_audit_row(score=0.72, label="genuine", flags=[])

    with (
        patch(
            "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
            return_value=audit_row,
        ),
        patch(
            "app.api.v2.authenticity.engine.score_single",
            new=AsyncMock(),
        ) as mock_engine,
    ):
        resp = client.post("/v2/authenticity", json={"text": _REVIEW_TEXT})

    assert resp.status_code == 200
    mock_engine.assert_not_called()


def test_authenticity_cache_hit_returns_stored_score(client: TestClient) -> None:
    """Score in the response must match the stored audit row, not a fresh LLM call."""
    audit_row = _fake_audit_row(score=0.61, label="suspicious", flags=["generic_low_info"])

    with patch(
        "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
        return_value=audit_row,
    ):
        resp = client.post("/v2/authenticity", json={"text": _REVIEW_TEXT})

    body = resp.json()
    assert abs(body["score"] - 0.61) < 1e-6
    assert body["label"] == "suspicious"
    assert "generic_low_info" in body["flags"]


def test_authenticity_cache_hit_correct_response_shape(client: TestClient) -> None:
    """Response shape on cache-hit contains all required AuthenticityResult fields."""
    audit_row = _fake_audit_row()

    with patch(
        "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
        return_value=audit_row,
    ):
        resp = client.post("/v2/authenticity", json={"text": _REVIEW_TEXT})

    body = resp.json()
    for field in ("score", "label", "flags", "reasons", "review_hash", "scored_at"):
        assert field in body, f"Missing field {field!r} in cache-hit response"


# ---------------------------------------------------------------------------
# Cache miss: normal scoring path
# ---------------------------------------------------------------------------


def test_authenticity_cache_miss_calls_engine_once(client: TestClient) -> None:
    """When no audit exists, engine.score_single is called exactly once."""
    fake_result = _fake_engine_result()

    with (
        patch(
            "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
            return_value=None,
        ),
        patch(
            "app.api.v2.authenticity.engine.score_single",
            new=AsyncMock(return_value=fake_result),
        ) as mock_engine,
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg",
            new=MagicMock(return_value=None),
        ),
    ):
        resp = client.post("/v2/authenticity", json={"text": _REVIEW_TEXT})

    assert resp.status_code == 200
    mock_engine.assert_called_once()


def test_authenticity_cache_miss_saves_audit(client: TestClient) -> None:
    """On a cache miss, save_authenticity_audit_pg must be called to persist the result."""
    fake_result = _fake_engine_result(score=0.9)

    with (
        patch(
            "app.api.v2.authenticity.get_authenticity_audit_by_hash_pg",
            return_value=None,
        ),
        patch(
            "app.api.v2.authenticity.engine.score_single",
            new=AsyncMock(return_value=fake_result),
        ),
        patch(
            "app.api.v2.authenticity.save_authenticity_audit_pg",
            new=MagicMock(return_value=None),
        ) as mock_save,
    ):
        client.post("/v2/authenticity", json={"text": _REVIEW_TEXT})

    mock_save.assert_called_once()
    call_args = mock_save.call_args[0]
    assert call_args[0] == _ORG_ID
    assert call_args[1] == _REVIEW_HASH
    assert abs(call_args[2] - 0.9) < 1e-6


# ---------------------------------------------------------------------------
# Unit tests for _review_hash and _audit_row_to_result helpers
# ---------------------------------------------------------------------------


def test_review_hash_matches_engine_schema_hash() -> None:
    """_review_hash must produce the same sha256 hex as AuthenticityResult.from_signals uses."""
    from app.api.v2.authenticity import _review_hash

    text = "Consistent hashing test review."
    # The engine stores hashlib.sha256(review_text.encode()).hexdigest() in review_hash.
    expected = hashlib.sha256(text.encode()).hexdigest()
    assert _review_hash(text) == expected


def test_review_hash_is_deterministic() -> None:
    """Same text always yields the same hash."""
    from app.api.v2.authenticity import _review_hash

    text = "Determinism check."
    assert _review_hash(text) == _review_hash(text)


def test_audit_row_to_result_parses_known_flags() -> None:
    """Known flag strings are converted to AuthenticityFlag enum values."""
    from app.api.v2.authenticity import _audit_row_to_result

    row: dict[str, object] = {
        "score": 0.45,
        "label": "suspicious",
        "flags": ["incentivized_phrase", "generic_low_info"],
        "review_hash": _REVIEW_HASH,
    }
    result = _audit_row_to_result(row, _REVIEW_TEXT)
    assert AuthenticityFlag.INCENTIVIZED_PHRASE in result.flags
    assert AuthenticityFlag.GENERIC_LOW_INFO in result.flags


def test_audit_row_to_result_ignores_unknown_flags() -> None:
    """Unknown flag strings (future schema) are silently dropped — no ValueError raised."""
    from app.api.v2.authenticity import _audit_row_to_result

    row: dict[str, object] = {
        "score": 0.8,
        "label": "genuine",
        "flags": ["future_unknown_flag", "incentivized_phrase"],
        "review_hash": _REVIEW_HASH,
    }
    result = _audit_row_to_result(row, _REVIEW_TEXT)
    assert AuthenticityFlag.INCENTIVIZED_PHRASE in result.flags
    assert len(result.flags) == 1  # unknown flag dropped


def test_audit_row_to_result_handles_unknown_label() -> None:
    """An unrecognised label value defaults to GENUINE rather than raising ValueError."""
    from app.api.v2.authenticity import _audit_row_to_result

    row: dict[str, object] = {
        "score": 0.9,
        "label": "unrecognised_future_label",
        "flags": [],
        "review_hash": _REVIEW_HASH,
    }
    result = _audit_row_to_result(row, _REVIEW_TEXT)
    assert result.label == AuthenticityLabel.GENUINE


# ---------------------------------------------------------------------------
# get_authenticity_audit_by_hash_pg — storage helper unit tests
# ---------------------------------------------------------------------------


def test_get_authenticity_audit_by_hash_pg_cache_miss_returns_none() -> None:
    """Returns None when no matching row exists."""
    from unittest.mock import MagicMock, patch

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    from app.core.storage_pg import get_authenticity_audit_by_hash_pg

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = get_authenticity_audit_by_hash_pg(_ORG_ID, _REVIEW_HASH)

    assert result is None


def test_get_authenticity_audit_by_hash_pg_returns_row_dict() -> None:
    """Returns a dict with score/label/flags/review_hash when row exists."""
    import json
    from unittest.mock import MagicMock, patch

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = (
        0.77,
        "genuine",
        json.dumps(["incentivized_phrase"]),
        _REVIEW_HASH,
    )

    from app.core.storage_pg import get_authenticity_audit_by_hash_pg

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        result = get_authenticity_audit_by_hash_pg(_ORG_ID, _REVIEW_HASH)

    assert result is not None
    assert abs(result["score"] - 0.77) < 1e-6  # type: ignore[arg-type]
    assert result["label"] == "genuine"
    assert "incentivized_phrase" in result["flags"]  # type: ignore[operator]
    assert result["review_hash"] == _REVIEW_HASH


def test_get_authenticity_audit_by_hash_pg_sets_rls_context() -> None:
    """The helper must issue SET LOCAL ROLE and app.current_org_id before the SELECT."""
    from unittest.mock import MagicMock, patch

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    cur.fetchone.return_value = None

    from app.core.storage_pg import get_authenticity_audit_by_hash_pg

    with patch("app.core.storage_pg._db_connect", return_value=conn):
        get_authenticity_audit_by_hash_pg(_ORG_ID, _REVIEW_HASH)

    sqls = [c[0][0] for c in cur.execute.call_args_list]
    assert any("SET LOCAL ROLE" in s for s in sqls)
    assert any("app.current_org_id" in s for s in sqls)
