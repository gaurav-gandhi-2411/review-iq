"""Unit tests for app.core.logging — setup_logging() coverage."""
from __future__ import annotations

from unittest.mock import patch

from app.core.logging import setup_logging


def _patch_settings(environment: str = "production", log_level: str = "INFO"):
    from app.core.config import Settings

    s = Settings.model_construct(environment=environment, log_level=log_level)
    return patch("app.core.logging.get_settings", return_value=s)


def test_setup_logging_production_json_renderer() -> None:
    with _patch_settings(environment="production"):
        setup_logging()  # must not raise


def test_setup_logging_development_console_renderer() -> None:
    with _patch_settings(environment="development"):
        setup_logging()  # must not raise


def test_setup_logging_debug_level() -> None:
    with _patch_settings(log_level="DEBUG"):
        setup_logging()
