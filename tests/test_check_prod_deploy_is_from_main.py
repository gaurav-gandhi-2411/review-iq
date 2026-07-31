"""Unit tests for scripts/check_prod_deploy_is_from_main.py.

scripts/ has no __init__.py (matches this repo's existing convention), so the module
is imported by inserting its directory onto sys.path rather than as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import check_prod_deploy_is_from_main as mod  # noqa: E402 -- must follow the sys.path insert


def _response(status_code: int, json_body: dict) -> httpx.Response:
    return httpx.Response(
        status_code, json=json_body, request=httpx.Request("GET", "https://example.invalid/")
    )


def test_get_production_commit_hash_returns_none_without_credentials() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert mod.get_production_commit_hash() is None


def test_get_production_commit_hash_returns_none_on_api_failure() -> None:
    with patch.dict(
        "os.environ", {"CLOUDFLARE_API_TOKEN": "x", "CLOUDFLARE_ACCOUNT_ID": "y"}, clear=True
    ):
        with patch("httpx.get", return_value=_response(500, {})):
            assert mod.get_production_commit_hash() is None


def test_get_production_commit_hash_returns_none_when_no_production_deployment() -> None:
    with patch.dict(
        "os.environ", {"CLOUDFLARE_API_TOKEN": "x", "CLOUDFLARE_ACCOUNT_ID": "y"}, clear=True
    ):
        body = {"success": True, "result": [{"environment": "preview"}]}
        with patch("httpx.get", return_value=_response(200, body)):
            assert mod.get_production_commit_hash() is None


def test_get_production_commit_hash_extracts_sha() -> None:
    with patch.dict(
        "os.environ", {"CLOUDFLARE_API_TOKEN": "x", "CLOUDFLARE_ACCOUNT_ID": "y"}, clear=True
    ):
        body = {
            "success": True,
            "result": [
                {
                    "environment": "production",
                    "deployment_trigger": {"metadata": {"commit_hash": "abc123"}},
                }
            ],
        }
        with patch("httpx.get", return_value=_response(200, body)):
            assert mod.get_production_commit_hash() == "abc123"


def test_is_ancestor_of_main_true() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert mod.is_ancestor_of_main("abc123") is True


def test_is_ancestor_of_main_false() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert mod.is_ancestor_of_main("abc123") is False


def test_main_fails_closed_when_commit_hash_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    """The exact invariant this script exists for: 'couldn't verify' must be a
    failure, never a silent pass."""
    with patch("check_prod_deploy_is_from_main.get_production_commit_hash", return_value=None):
        exit_code = mod.main()
    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_fails_when_commit_not_on_main(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "check_prod_deploy_is_from_main.get_production_commit_hash", return_value="deadbeef"
    ):
        with patch("check_prod_deploy_is_from_main.is_ancestor_of_main", return_value=False):
            exit_code = mod.main()
    assert exit_code == 1
    assert "NOT an ancestor" in capsys.readouterr().out


def test_main_passes_when_commit_is_on_main(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "check_prod_deploy_is_from_main.get_production_commit_hash", return_value="deadbeef"
    ):
        with patch("check_prod_deploy_is_from_main.is_ancestor_of_main", return_value=True):
            exit_code = mod.main()
    assert exit_code == 0
    assert "OK" in capsys.readouterr().out
