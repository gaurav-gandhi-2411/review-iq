"""API key generation.

Used by admin endpoints (Step 5) when creating a new api_keys row.
The raw key is shown to the caller exactly once; only the prefix and hash are stored.
"""
from __future__ import annotations

import secrets

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
