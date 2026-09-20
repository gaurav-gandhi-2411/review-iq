"""Unit tests for the get_authenticity_audit_by_hash_pg storage helper (pre-LLM cache lookup).

The POST /v2/authenticity route tests that used to live here were removed with the route
(Session 15d, D5). The helper itself is still used by the dashboard's POST /bff/authenticity.
"""

from __future__ import annotations

import hashlib
import uuid

_ORG_ID = str(uuid.uuid4())
_REVIEW_TEXT = "This product is the best I have ever used, highly recommend!"
_REVIEW_HASH = hashlib.sha256(_REVIEW_TEXT.encode()).hexdigest()


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
