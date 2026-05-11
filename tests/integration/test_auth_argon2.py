"""Integration tests: real argon2id verification and monthly quota reset.

Proves:
  1. A correct key authenticates successfully (real hash is verified).
  2. A key with the same prefix but wrong suffix is rejected 401 — argon2 is the
     real auth step, not just the prefix lookup.
  3. usage_records from a prior month do not count toward the current month's quota.

Marked 'integration' — requires live Supabase DB.
Run: uv run pytest tests/integration/test_auth_argon2.py -v -m integration
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv
from fastapi import HTTPException

from app.auth.api_key import _lookup_and_record
from app.auth.keygen import generate_api_key

load_dotenv(Path(__file__).parents[2] / ".env")

_DIRECT_URL = os.environ["SUPABASE_DIRECT_URL"]


def _direct_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(_DIRECT_URL)


def _setup(conn: psycopg2.extensions.connection, org_id: str, key_id: str,
           key_prefix: str, key_hash: str, quota: int = 10) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO public.organizations (id, name, slug) VALUES (%s, %s, %s)",
        (org_id, "ArgonTest", f"argon-{org_id[:8]}"),
    )
    cur.execute(
        "INSERT INTO public.api_keys "
        "(id, org_id, key_prefix, key_hash, name, quota) "
        "VALUES (%s, %s, %s, %s, 'argon-test', %s)",
        (key_id, org_id, key_prefix, key_hash, quota),
    )
    conn.commit()


def _teardown(org_id: str) -> None:
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.integration
def test_correct_key_succeeds() -> None:
    """A valid raw key verified against its real argon2id hash returns ApiKeyContext."""
    raw_key, key_prefix, key_hash = generate_api_key()
    org_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    conn = _direct_conn()
    try:
        _setup(conn, org_id, key_id, key_prefix, key_hash)
    finally:
        conn.close()

    try:
        ctx = _lookup_and_record(raw_key)
        assert ctx.org_id == org_id
        assert ctx.api_key_id == key_id
        assert ctx.key_name == "argon-test"
    finally:
        _teardown(org_id)


@pytest.mark.integration
def test_same_prefix_wrong_key_fails_401() -> None:
    """A key sharing only the prefix but with a different suffix must be rejected.

    This proves argon2 verification is the real auth gate, not just the prefix lookup.
    If prefix lookup alone gated access, this call would succeed.
    """
    raw_key, key_prefix, key_hash = generate_api_key()
    org_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    conn = _direct_conn()
    try:
        _setup(conn, org_id, key_id, key_prefix, key_hash)
    finally:
        conn.close()

    # Flip the last hex character — same 17-char prefix, different raw key
    last = raw_key[-1]
    wrong_key = raw_key[:-1] + ("0" if last != "0" else "1")
    assert wrong_key[:17] == key_prefix  # same prefix
    assert wrong_key != raw_key          # different key

    try:
        with pytest.raises(HTTPException) as exc:
            _lookup_and_record(wrong_key)
        assert exc.value.status_code == 401
    finally:
        _teardown(org_id)


@pytest.mark.integration
def test_monthly_quota_resets_across_months() -> None:
    """usage_records timestamped last month do not consume this month's quota.

    Flow:
      1. Create key with quota=2.
      2. Fire 2 requests → both succeed, quota exhausted.
      3. Fire 1 more → 429.
      4. Back-date all usage_records to last month via direct UPDATE.
      5. Fire 1 more → succeeds (new month, counter reset).
    """
    raw_key, key_prefix, key_hash = generate_api_key()
    org_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())
    QUOTA = 2

    conn = _direct_conn()
    try:
        _setup(conn, org_id, key_id, key_prefix, key_hash, quota=QUOTA)
    finally:
        conn.close()

    try:
        # Exhaust quota
        for _ in range(QUOTA):
            _lookup_and_record(raw_key)

        # Confirm quota is exhausted
        with pytest.raises(HTTPException) as exc:
            _lookup_and_record(raw_key)
        assert exc.value.status_code == 429

        # Time-travel: move all usage_records for this key to last month
        conn = _direct_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE public.usage_records "
                "SET created_at = now() - interval '1 month' "
                "WHERE api_key_id = %s",
                (key_id,),
            )
            conn.commit()
        finally:
            conn.close()

        # New month — quota resets automatically via date_trunc
        ctx = _lookup_and_record(raw_key)
        assert ctx.org_id == org_id

    finally:
        _teardown(org_id)
