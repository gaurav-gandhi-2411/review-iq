"""The private classify service (app/classify_service.py): no API key, no database, typed 503, no docs surface (S27)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

import httpx  # noqa: E402
from app import classify_service  # noqa: E402
from app.core.classify import runtime  # noqa: E402

from tests.unit.test_v2_classify import TH, FakeBackend  # noqa: E402


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setattr(runtime, "_state", {"backend": FakeBackend(), "th": TH})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=classify_service.app), base_url="http://test"
    ) as c:
        yield c


def _body(*texts: str) -> dict:
    return {"reviews": [{"id": f"r{i}", "text": t} for i, t in enumerate(texts)]}


@pytest.mark.asyncio
async def test_classifies_without_any_credential_and_without_touching_the_database(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    psycopg2 = pytest.importorskip("psycopg2")

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("the private service must never open a database connection")

    monkeypatch.setattr(psycopg2, "connect", boom)
    r = await client.post(
        "/v2/classify", json=_body("good", "also good")
    )  # no Authorization, no X-API-Key
    assert r.status_code == 200
    d = r.json()
    assert d["decision"]["thresholds_sha256"] == TH.sha256
    assert [x["id"] for x in d["results"]] == ["r0", "r1"]
    assert set(d["results"][0]["tasks"]) == set(runtime.TASKS)


@pytest.mark.asyncio
async def test_does_not_depend_on_the_public_flag(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.v2.classify.get_settings",
        lambda: SimpleNamespace(
            enable_v2_classify=False, classify_artifact_dir="/nonexistent", classify_torch_threads=0
        ),
    )
    assert (await client.post("/v2/classify", json=_body("x"))).status_code == 200


@pytest.mark.asyncio
async def test_typed_503_when_artifacts_are_missing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "_state", {})
    monkeypatch.setattr(
        "app.api.v2.classify.get_settings",
        lambda: SimpleNamespace(
            enable_v2_classify=False, classify_artifact_dir="/nonexistent", classify_torch_threads=0
        ),
    )
    r = await client.post("/v2/classify", json=_body("x"))
    assert r.status_code == 503 and r.json()["code"] == "classify_artifacts_missing"
    assert r.headers.get("retry-after") == "60"


@pytest.mark.asyncio
async def test_validation_and_no_docs_surface(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v2/classify", json={"reviews": []})).status_code == 422
    assert (await client.post("/v2/classify", json=_body("   "))).status_code == 422
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404
    assert (await client.get("/healthz")).json() == {"status": "ok"}
