"""Unit tests for create_app() — deploy-target and service-role router gating."""

from __future__ import annotations

from app.core.config import Settings
from app.main import create_app
from starlette.testclient import TestClient


def _app(deploy_target: str, service_role: str = "public") -> object:
    return create_app(
        settings=Settings.model_construct(
            deploy_target=deploy_target,
            service_role=service_role,
            rate_limit_per_minute=30,
        )
    )


def _paths(deploy_target: str, service_role: str = "public") -> set[str]:
    return {r.path for r in _app(deploy_target, service_role).routes if hasattr(r, "path")}


def test_cloud_run_mounts_v2_not_admin() -> None:
    """Wave 1 S0 remediation (ADR 0006): the public service never mounts admin_router,
    regardless of deploy_target -- that's now exclusively SERVICE_ROLE=admin's job."""
    paths = _paths("cloud-run")
    assert "/v2/extract" in paths
    assert "/v2/extract/batch" in paths
    assert "/admin/organizations" not in paths
    assert "/health" in paths
    assert "/metrics" in paths


def test_cloud_run_v1_returns_404() -> None:
    """v1 routes must 404 on cloud-run — HTTP-level, not just absent from app.routes."""
    client = TestClient(_app("cloud-run"), raise_server_exceptions=False)
    assert client.post("/extract", json={"review": "test"}).status_code == 404
    assert client.post("/extract/batch", json={"reviews": []}).status_code == 404


def test_local_mounts_v1_and_v2_not_admin() -> None:
    paths = _paths("local")
    assert "/extract" in paths
    assert "/v2/extract" in paths
    assert "/admin/organizations" not in paths
    assert "/health" in paths


def test_hf_spaces_mounts_v1_and_v2() -> None:
    paths = _paths("hf-spaces")
    assert "/extract" in paths
    assert "/v2/extract" in paths


def test_admin_service_role_mounts_only_ops_and_admin() -> None:
    """Wave 1 S0 remediation (ADR 0006): SERVICE_ROLE=admin mounts nothing
    public-facing -- only the health/metrics ops routes and admin_router itself, so a
    misconfigured IAM binding on this service has no other surface to expose."""
    paths = _paths("cloud-run", service_role="admin")
    assert "/admin/organizations" in paths
    assert "/health" in paths
    assert "/metrics" in paths
    assert "/v2/extract" not in paths
    assert "/webhooks/google/reviews" not in paths
    assert "/demo/extract" not in paths
