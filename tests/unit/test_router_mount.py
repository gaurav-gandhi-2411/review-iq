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


def _walk(routes: list[object], prefix: str = "") -> set[str]:
    # FastAPI >= 0.141 no longer flattens include_router() into app.routes: each include
    # becomes an _IncludedRouter wrapper (no .path) holding the real router + its prefix.
    # Older versions expose flat routes. Handle both so this asserts on real mounted paths
    # (unlike app.openapi(), which omits include_in_schema=False routes such as /health and
    # would make the negative "not in paths" assertions below pass vacuously).
    out: set[str] = set()
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            out |= _walk(inner.routes, prefix + (getattr(r.include_context, "prefix", "") or ""))
        elif hasattr(r, "path"):
            out.add(prefix + r.path)
    return out


def _paths(deploy_target: str, service_role: str = "public") -> set[str]:
    return _walk(_app(deploy_target, service_role).routes)


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
    assert "/leads" not in paths


def test_public_service_mounts_leads_on_every_deploy_target() -> None:
    """Session 15c C9: the marketing-site lead-capture route lives on the PUBLIC service
    only (the admin service, above, must not expose it)."""
    for deploy_target in ("cloud-run", "local"):
        assert "/leads" in _paths(deploy_target, service_role="public")


# The fake-review flag is unmeasurable (Session 15c D2), so its public API routes were removed
# in Session 15d (D5) and the dashboard's /bff/* authenticity routes in Session 17 (W6).
_REMOVED_AUTHENTICITY_PATHS = (
    "/v2/authenticity",
    "/v2/authenticity/batch",
    "/v2/insights/authenticity",
    "/bff/authenticity",
    "/bff/authenticity/flagged",
    "/bff/insights/authenticity",
)


def test_public_service_does_not_mount_removed_authenticity_routes() -> None:
    """Session 15d D5 + Session 17 W6: no public deploy target mounts any /v2 or /bff
    authenticity route; the sibling /v2/insights and /bff/insights endpoints are unaffected."""
    for deploy_target in ("cloud-run", "local", "hf-spaces"):
        paths = _paths(deploy_target, service_role="public")
        for removed in _REMOVED_AUTHENTICITY_PATHS:
            assert removed not in paths, f"{removed} is still mounted on {deploy_target}"
        assert "/v2/insights/trends" in paths
        assert "/v2/insights/health-score" in paths
        assert "/bff/insights/health-score" in paths


def test_removed_authenticity_routes_return_404() -> None:
    """HTTP-level proof (not just absent from app.routes): the removed routes 404 with no auth."""
    client = TestClient(_app("cloud-run"), raise_server_exceptions=False)
    assert client.post("/v2/authenticity", json={"text": "x"}).status_code == 404
    assert client.post("/v2/authenticity/batch", json={"reviews": []}).status_code == 404
    assert client.get("/v2/insights/authenticity").status_code == 404
    assert client.post("/bff/authenticity", json={"text": "x"}).status_code == 404
    assert client.get("/bff/authenticity/flagged").status_code == 404
    assert client.get("/bff/insights/authenticity").status_code == 404


def test_admin_service_role_unaffected_by_authenticity_removal() -> None:
    """The admin service never mounted these routes; it still mounts exactly ops + admin."""
    paths = _paths("cloud-run", service_role="admin")
    assert "/admin/organizations" in paths
    for removed in _REMOVED_AUTHENTICITY_PATHS:
        assert removed not in paths


def test_openapi_has_no_v2_authenticity_paths_and_no_fake_review_claim() -> None:
    """Regression (D5): app.openapi() carries no /v2/authenticity* or /v2/insights/authenticity
    path, no v2-authenticity tag, and no 'fake-review' promise anywhere in the schema."""
    import json

    schema = _app("cloud-run").openapi()  # type: ignore[attr-defined]
    for path in schema["paths"]:
        assert not path.startswith("/v2/authenticity"), path
        assert path != "/v2/insights/authenticity", path
    assert "v2-authenticity" not in {t["name"] for t in schema.get("tags", [])}
    blob = json.dumps(schema).lower()
    assert "fake-review" not in blob
    assert "fake review" not in blob


def test_openapi_has_no_authenticity_in_paths_ingest_or_health_score() -> None:
    """Session 17 W6: no authenticity path, and neither CSV ingest (include_authenticity) nor
    the health-score operation mentions authenticity. NOT asserted for the whole schema:
    /v2/dataset and the corrections SourceType enum still reference stored audit rows (tracked
    follow-up, deliberately out of W6's scope)."""
    import json

    schema = _app("cloud-run").openapi()  # type: ignore[attr-defined]
    assert not [p for p in schema["paths"] if "authenticity" in p]
    for path in ("/v2/ingest/csv", "/v2/insights/health-score"):
        assert "authenticity" not in json.dumps(schema["paths"][path]).lower(), path
