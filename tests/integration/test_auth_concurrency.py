"""Concurrency test: atomic quota enforcement under simultaneous requests.

Proves the UPDATE...WHERE usage < quota RETURNING pattern has no TOCTOU race.
Fires N requests against a key with quota=N-1; asserts exactly N-1 succeed.

Marked 'integration' — requires live Supabase DB.
Run: uv run pytest tests/integration/test_auth_concurrency.py -v -m integration
"""
from __future__ import annotations

import asyncio
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_quota_enforcement() -> None:
    """Exactly QUOTA requests succeed; the rest return 429 — no over-admission."""
    N = 6
    QUOTA = N - 1  # 5

    raw_key, key_prefix, key_hash = generate_api_key()
    org_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    # --- Setup (direct connection, bypasses RLS) ---
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO public.organizations (id, name, slug) VALUES (%s, 'Concurrency Test', %s)",
            (org_id, f"concurrency-{org_id[:8]}"),
        )
        cur.execute(
            "INSERT INTO public.api_keys "
            "(id, org_id, key_prefix, key_hash, name, quota) "
            "VALUES (%s, %s, %s, %s, 'concurrency-test', %s)",
            (key_id, org_id, key_prefix, key_hash, QUOTA),
        )
        conn.commit()
    finally:
        conn.close()

    # --- Fire N concurrent requests ---
    async def _call() -> str:
        try:
            await asyncio.to_thread(_lookup_and_record, raw_key)
            return "ok"
        except HTTPException as exc:
            return str(exc.status_code)

    results = await asyncio.gather(*[_call() for _ in range(N)])

    # --- Teardown (CASCADE deletes api_keys, usage_records) ---
    conn = _direct_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM public.organizations WHERE id = %s", (org_id,))
        conn.commit()
    finally:
        conn.close()

    ok_count = results.count("ok")
    quota_count = results.count("429")

    assert ok_count == QUOTA, (
        f"Expected {QUOTA} successes, got {ok_count}. "
        f"Full results: {results}"
    )
    assert quota_count == N - QUOTA, (
        f"Expected {N - QUOTA} quota rejections, got {quota_count}. "
        f"Full results: {results}"
    )
