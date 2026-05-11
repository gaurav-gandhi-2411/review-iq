"""Unit tests for create_app() — deploy-target router gating."""

from __future__ import annotations

from starlette.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _app(deploy_target: str) -> object:
    return create_app(settings=Settings.model_construct(
        deploy_target=deploy_target, rate_limit_per_minute=30
    ))


def _paths(deploy_target: str) -> set[str]:
    return {r.path for r in _app(deploy_target).routes if hasattr(r, "path")}


def test_cloud_run_mounts_v2_and_admin() -> None:
    paths = _paths("cloud-run")
    assert "/v2/extract" in paths
    assert "/v2/extract/batch" in paths
    assert "/admin/organizations" in paths
    assert "/health" in paths
    assert "/metrics" in paths


def test_cloud_run_v1_returns_404() -> None:
    """v1 routes must 404 on cloud-run — HTTP-level, not just absent from app.routes."""
    client = TestClient(_app("cloud-run"), raise_server_exceptions=False)
    assert client.post("/extract", json={"review": "test"}).status_code == 404
    assert client.post("/extract/batch", json={"reviews": []}).status_code == 404


def test_local_mounts_v1_and_v2() -> None:
    paths = _paths("local")
    assert "/extract" in paths
    assert "/v2/extract" in paths
    assert "/admin/organizations" in paths
    assert "/health" in paths


def test_hf_spaces_mounts_v1_and_v2() -> None:
    paths = _paths("hf-spaces")
    assert "/extract" in paths
    assert "/v2/extract" in paths
