from __future__ import annotations

import sys
import types

import pytest
from app.auth import signup
from app.core.config import Settings


def _client_key(monkeypatch: pytest.MonkeyPatch, **env: str) -> str:
    seen: dict[str, str] = {}
    fake = types.SimpleNamespace(create_client=lambda url, key: seen.update(url=url, key=key))
    monkeypatch.setitem(sys.modules, "supabase", fake)
    s = Settings(_env_file=None, SUPABASE_URL="https://x.example", **env)  # type: ignore[call-arg]
    monkeypatch.setattr(signup, "get_settings", lambda: s)
    signup._get_supabase_admin()
    return seen["key"]


def test_prefers_anon_key_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _client_key(monkeypatch, SUPABASE_ANON_KEY="anon-k", SUPABASE_SERVICE_ROLE_KEY="svc-k")
    assert key == "anon-k"


def test_falls_back_to_service_role_until_anon_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _client_key(monkeypatch, SUPABASE_SERVICE_ROLE_KEY="svc-k") == "svc-k"
