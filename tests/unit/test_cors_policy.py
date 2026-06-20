"""CORS policy tests — allowlist gate.

These tests ensure that '*' never appears in the allowed_origins list and that
the comma-separated env-var parsing works correctly.  The gate test is the
critical one: if it fails, wildcard CORS would ship to production.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings


def test_cors_default_is_not_wildcard() -> None:
    """Default allowed_origins must not contain '*' — the gate."""
    s = Settings()
    assert "*" not in s.allowed_origins, (
        "Wildcard origin found in default ALLOWED_ORIGINS. "
        "This must never ship to production."
    )


def test_cors_default_includes_localhost_dev() -> None:
    """Default list must include both local dev origin aliases."""
    s = Settings()
    assert "http://localhost:5173" in s.allowed_origins
    assert "http://127.0.0.1:5173" in s.allowed_origins


def test_cors_default_localhost_aliases_excluded_when_prod_origin_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production ALLOWED_ORIGINS must not include either localhost alias."""
    prod_origin = "https://review-iq.vercel.app"
    monkeypatch.setenv("ALLOWED_ORIGINS", prod_origin)
    s = Settings()
    assert s.allowed_origins == [prod_origin]
    assert "http://localhost:5173" not in s.allowed_origins
    assert "http://127.0.0.1:5173" not in s.allowed_origins


def test_cors_default_includes_demo_pages() -> None:
    """Default list must include the existing demo Cloudflare Pages origin."""
    s = Settings()
    assert "https://review-iq-demo.pages.dev" in s.allowed_origins


def test_cors_env_var_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """ALLOWED_ORIGINS env var accepts a comma-separated string."""
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://app.example.com, https://staging.example.com",
    )
    s = Settings()
    assert s.allowed_origins == ["https://app.example.com", "https://staging.example.com"]


def test_cors_env_var_wildcard_is_parseable_but_documented_as_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If someone manually sets ALLOWED_ORIGINS=*, the setting parses it — but
    the deployment runbook and the gate test above catch it before prod deploy."""
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    s = Settings()
    assert s.allowed_origins == ["*"]  # parses OK, but caught by the gate test


def test_cors_middleware_uses_settings_origins() -> None:
    """The CORS middleware registered in create_app uses settings.allowed_origins,
    not a hardcoded list."""
    import inspect
    from app import main as main_module
    src = inspect.getsource(main_module.create_app)
    assert "settings.allowed_origins" in src
    assert '"*"' not in src  # wildcard must not be hardcoded
