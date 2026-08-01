"""API key generation.

Used by admin endpoints (Step 5) when creating a new api_keys row.
The raw key is shown to the caller exactly once; only the prefix and hash are stored.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

import psycopg2.errors
from argon2 import PasswordHasher

_PH = PasswordHasher()  # argon2id, defaults: time=3, memory=64MB, parallelism=4

_KEY_PREFIX_LEN = 17  # "riq_live_" (9) + 8 hex chars


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, key_prefix, key_hash).

    raw_key    — riq_live_<32 hex chars>; show to caller once, never persist
    key_prefix — raw_key[:17]; store indexed for O(1) candidate lookup
    key_hash   — argon2id(raw_key); store for constant-time verification
    """
    raw_key = f"riq_live_{secrets.token_hex(16)}"
    key_prefix = raw_key[:_KEY_PREFIX_LEN]
    key_hash = _PH.hash(raw_key)
    return raw_key, key_prefix, key_hash


# ---------------------------------------------------------------------------
# key_prefix collision retry (BYPASSRLS remediation pass, 2026-08-01)
#
# key_prefix is riq_live_ + 8 hex chars = 32 bits of entropy -- meaningfully
# collision-prone at scale (birthday bound ~65k keys for a 50% collision chance).
# api_keys.key_prefix now carries a real UNIQUE constraint (see
# supabase/migrations/20260801000001_role_separation_bypassrls_remediation.sql) that
# was previously only an assumption every lookup site made, never enforced. This
# closes the resulting availability bug (a collision would previously have let
# INSERT succeed with two rows sharing a prefix, and app/auth/api_key.py's
# `fetchone()` on that prefix would non-deterministically pick one -- correctness
# rested entirely on downstream argon2id verification catching the mismatch,
# which it does, but that's a confusing 401 for a legitimate caller, not a clean fix).
# ---------------------------------------------------------------------------

_MAX_KEY_GENERATION_ATTEMPTS = 5


def _is_key_prefix_collision(exc: psycopg2.errors.UniqueViolation) -> bool:
    """True iff `exc` is specifically the api_keys_key_prefix_key violation -- never
    swallow an unrelated UniqueViolation (e.g. a real caller bug) as if it were this."""
    diag = getattr(exc, "diag", None)
    return getattr(diag, "constraint_name", None) == "api_keys_key_prefix_key"


def insert_api_key_with_retry(
    cur: Any, execute_insert: Callable[[str, str, str], None]
) -> tuple[str, str, str]:
    """Generate a fresh API key and call `execute_insert(raw_key, key_prefix, key_hash)`
    (which must run exactly one INSERT into api_keys using these three values),
    retrying with a newly generated key on a key_prefix collision. Returns the
    (raw_key, key_prefix, key_hash) that succeeded.

    SAVEPOINT-scoped: a collision retry rolls back only the failed INSERT, not the
    caller's whole transaction (e.g. an earlier org-existence check in the same
    transaction survives). Raises on any other error, or after
    _MAX_KEY_GENERATION_ATTEMPTS collisions (which would mean something is wrong with
    the entropy source, not ordinary bad luck).
    """
    for attempt in range(_MAX_KEY_GENERATION_ATTEMPTS):
        raw_key, key_prefix, key_hash = generate_api_key()
        cur.execute("SAVEPOINT key_insert_retry")
        try:
            execute_insert(raw_key, key_prefix, key_hash)
        except psycopg2.errors.UniqueViolation as exc:
            cur.execute("ROLLBACK TO SAVEPOINT key_insert_retry")
            if not _is_key_prefix_collision(exc) or attempt == _MAX_KEY_GENERATION_ATTEMPTS - 1:
                raise
            continue
        cur.execute("RELEASE SAVEPOINT key_insert_retry")
        return raw_key, key_prefix, key_hash
    raise AssertionError("unreachable — loop above always returns or raises")
