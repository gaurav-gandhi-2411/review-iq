"""Unit tests for app.auth.keygen.insert_api_key_with_retry.

Covers the collision-retry loop directly (BYPASSRLS remediation, ADDITION 2):
api_keys.key_prefix now carries a real UNIQUE constraint, so a collision on
generation must retry with a fresh key rather than surface as a raw DB error
or a silent duplicate-prefix row.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2.errors
import pytest
from app.auth.keygen import _MAX_KEY_GENERATION_ATTEMPTS, insert_api_key_with_retry


class _PrefixCollision(psycopg2.errors.UniqueViolation):
    """A UniqueViolation whose diag.constraint_name reports the key_prefix constraint.

    psycopg2's real `diag` attribute is populated by the C extension from a live
    connection and isn't writable on a bare instance, so this subclass overrides
    the `diag` property to fake it for tests.
    """

    @property
    def diag(self) -> SimpleNamespace:
        return SimpleNamespace(constraint_name="api_keys_key_prefix_key")


class _OtherCollision(psycopg2.errors.UniqueViolation):
    """A UniqueViolation on some other constraint -- must never be retried as if
    it were a key_prefix collision."""

    @property
    def diag(self) -> SimpleNamespace:
        return SimpleNamespace(constraint_name="api_keys_pkey")


def _make_cur() -> MagicMock:
    return MagicMock()


def test_succeeds_first_attempt_no_retry() -> None:
    cur = _make_cur()
    execute_insert = MagicMock()

    with patch(
        "app.auth.keygen.generate_api_key",
        return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash"),
    ):
        result = insert_api_key_with_retry(cur, execute_insert)

    assert result == ("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash")
    execute_insert.assert_called_once_with("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash")
    savepoint_sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert savepoint_sqls == ["SAVEPOINT key_insert_retry", "RELEASE SAVEPOINT key_insert_retry"]


def test_retries_on_key_prefix_collision_then_succeeds() -> None:
    cur = _make_cur()
    execute_insert = MagicMock(side_effect=[_PrefixCollision(), None])
    keys = [
        ("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash_a"),
        ("riq_live_" + "b" * 32, "riq_live_bbbbbbbb", "hash_b"),
    ]

    with patch("app.auth.keygen.generate_api_key", side_effect=keys):
        result = insert_api_key_with_retry(cur, execute_insert)

    assert result == keys[1]
    assert execute_insert.call_count == 2
    savepoint_sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert savepoint_sqls == [
        "SAVEPOINT key_insert_retry",
        "ROLLBACK TO SAVEPOINT key_insert_retry",
        "SAVEPOINT key_insert_retry",
        "RELEASE SAVEPOINT key_insert_retry",
    ]


def test_non_prefix_collision_reraises_immediately_no_retry() -> None:
    cur = _make_cur()
    exc = _OtherCollision()
    execute_insert = MagicMock(side_effect=exc)

    with patch(
        "app.auth.keygen.generate_api_key",
        return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash"),
    ):
        with pytest.raises(_OtherCollision):
            insert_api_key_with_retry(cur, execute_insert)

    execute_insert.assert_called_once()
    savepoint_sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert savepoint_sqls == [
        "SAVEPOINT key_insert_retry",
        "ROLLBACK TO SAVEPOINT key_insert_retry",
    ]


def test_exhausts_max_attempts_raises_last_collision() -> None:
    cur = _make_cur()
    execute_insert = MagicMock(
        side_effect=[_PrefixCollision() for _ in range(_MAX_KEY_GENERATION_ATTEMPTS)]
    )

    with patch(
        "app.auth.keygen.generate_api_key",
        return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash"),
    ):
        with pytest.raises(_PrefixCollision):
            insert_api_key_with_retry(cur, execute_insert)

    assert execute_insert.call_count == _MAX_KEY_GENERATION_ATTEMPTS


def test_non_unique_violation_propagates_untouched() -> None:
    cur = _make_cur()
    execute_insert = MagicMock(side_effect=RuntimeError("disk full"))

    with patch(
        "app.auth.keygen.generate_api_key",
        return_value=("riq_live_" + "a" * 32, "riq_live_aaaaaaaa", "hash"),
    ):
        with pytest.raises(RuntimeError, match="disk full"):
            insert_api_key_with_retry(cur, execute_insert)

    # No ROLLBACK TO SAVEPOINT -- only UniqueViolation is caught inside the loop.
    savepoint_sqls = [c.args[0] for c in cur.execute.call_args_list]
    assert savepoint_sqls == ["SAVEPOINT key_insert_retry"]
