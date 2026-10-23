"""Encoder-first routing (spec Amendment 14): router R1/R2, capacity arithmetic, fail-open, flag-off no-op."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.core.classify import encoder_first as ef


def _t(na: str, urg: str, urg_cand: str | None = None) -> dict[str, dict[str, Any]]:
    return {
        "needs_action": {"label": na, "candidate_label": na},
        "urgency": {"label": urg, "candidate_label": urg_cand or urg},
    }


@pytest.mark.parametrize(
    ("tasks", "route", "reasons"),
    [
        (_t("no", "low"), False, ()),
        (_t("yes", "low"), True, ("needs_action_yes",)),
        (_t("abstained", "low"), True, ("needs_action_abstained",)),
        (_t("no", "high"), True, ("urgency_high_candidate",)),
        # high by threshold but abstained overall: both clauses fire (the safety reading is candidate_label)
        (_t("no", "abstained", "high"), True, ("urgency_high_candidate", "urgency_abstained")),
        (_t("no", "abstained", "medium"), True, ("urgency_abstained",)),
        (_t("no", "medium"), False, ()),
    ],
)
def test_r1(tasks: dict, route: bool, reasons: tuple[str, ...]) -> None:
    d = ef.route_r1(tasks)
    assert (d.route, d.reasons) == (route, reasons)


def test_r2_drops_only_the_urgency_abstained_clause() -> None:
    assert ef.route_r2(_t("no", "abstained", "medium")).route is False
    assert ef.route_r2(_t("no", "abstained", "high")).route is True
    assert ef.route_r2(_t("yes", "low")).route is True


def test_capacity_multiplier() -> None:
    assert ef.capacity_multiplier(0.5, 0.0) == pytest.approx(2.0)
    assert ef.capacity_multiplier(1.0, 0.3) == pytest.approx(1.0)  # nothing skipped: no gain
    assert ef.capacity_multiplier(0.25, 0.1) == pytest.approx(1 / (0.25 + 0.075))


class _Fake:
    def __init__(self, out: list | Exception) -> None:
        self.out = out

    async def classify(self, texts: list[str]) -> list:
        if isinstance(self.out, Exception):
            raise self.out
        return self.out

    async def classify_full(self, texts: list[str]) -> tuple[list, str]:
        return await self.classify(texts), "a" * 64


@pytest.mark.asyncio
async def test_plan_routes_and_fail_open() -> None:
    ok = await ef.plan_routes(_Fake([_t("no", "low"), _t("yes", "low")]), ["a", "b"])
    assert [d.route for d in ok] == [False, True]
    for bad in (
        _Fake(RuntimeError("down")),
        _Fake([_t("no", "low")]),
        _Fake([{"x": {}}, {"y": {}}]),
    ):
        out = await ef.plan_routes(bad, ["a", "b"])  # error / wrong count / malformed answer
        assert [d.route for d in out] == [True, True]
        assert out[0].reasons == ("classifier_unavailable",)


@pytest.mark.asyncio
async def test_should_extract_is_a_no_op_when_off_or_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for s in (
        SimpleNamespace(enable_encoder_first=False, classify_service_url="https://x"),
        SimpleNamespace(enable_encoder_first=True, classify_service_url=""),
    ):
        monkeypatch.setattr("app.core.config.get_settings", lambda s=s: s)
        monkeypatch.setattr(ef, "_remote", None)
        assert await ef.should_extract("any text") is True
        assert ef._remote is None  # no client was even created


@pytest.mark.asyncio
async def test_should_extract_skips_classified_only_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = SimpleNamespace(enable_encoder_first=True, classify_service_url="https://svc.example")
    monkeypatch.setattr("app.core.config.get_settings", lambda: s)
    fake = _Fake([_t("no", "low")])
    fake._url = "https://svc.example"  # type: ignore[attr-defined]
    monkeypatch.setattr(ef, "_remote", fake)
    assert await ef.should_extract("t") is False
    fake.out = RuntimeError("boom")
    assert await ef.should_extract("t") is True  # fail-open


@pytest.mark.asyncio
async def test_plan_ingest_returns_the_decisions_and_thresholds_hash_to_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = SimpleNamespace(enable_encoder_first=True, classify_service_url="https://svc.example")
    monkeypatch.setattr("app.core.config.get_settings", lambda: s)
    fake = _Fake([_t("no", "low")])
    fake._url = "https://svc.example"  # type: ignore[attr-defined]
    monkeypatch.setattr(ef, "_remote", fake)
    skipped = await ef.plan_ingest("t")
    assert skipped.route is False and skipped.thresholds_sha256 == "a" * 64
    assert skipped.tasks == _t("no", "low")
    fake.out = [_t("yes", "low")]
    assert (await ef.plan_ingest("t")).route is True
    fake.out = RuntimeError("boom")
    failed = await ef.plan_ingest("t")
    assert failed == ef.IngestPlan(True)  # fail-open: nothing to persist, extract now
    fake.out = [_t("no", "low"), _t("no", "low")]  # wrong number of answers
    assert (await ef.plan_ingest("t")).route is True


@pytest.mark.asyncio
async def test_plan_ingest_flag_off_is_the_plain_extract_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = SimpleNamespace(enable_encoder_first=False, classify_service_url="https://svc.example")
    monkeypatch.setattr("app.core.config.get_settings", lambda: s)
    monkeypatch.setattr(ef, "_remote", None)
    assert await ef.plan_ingest("t") == ef.IngestPlan(True)
    assert ef._remote is None


def test_ingest_path_timeout_covers_a_cold_start_and_the_default_stays_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # measured Cloud Run cold start 15.2 s (S29): the background ingest path must outlast it with margin; the class default (the
    # user-facing choice) must not silently grow
    assert ef.INGEST_TIMEOUT_S >= 3 * 15.2
    assert ef.RemoteClassifier("https://svc.example")._timeout == 10.0
    s = SimpleNamespace(enable_encoder_first=True, classify_service_url="https://svc.example")
    monkeypatch.setattr("app.core.config.get_settings", lambda: s)
    monkeypatch.setattr(ef, "_remote", None)

    async def fake(self: object, texts: list[str]) -> tuple[list, str]:
        return [_t("no", "low")], "a" * 64

    monkeypatch.setattr(
        ef.RemoteClassifier, "classify_full", fake
    )  # plan_ingest calls classify_full (#578)
    import asyncio

    assert asyncio.run(ef.should_extract("t")) is False
    assert ef._remote is not None and ef._remote._timeout == ef.INGEST_TIMEOUT_S


def test_the_http_client_is_built_with_the_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # S31 control audit: a client that hard-coded its own timeout survived the test above, which only reads the attribute
    seen: dict = {}

    class StopClient:
        def __init__(self, **kw: object) -> None:
            seen.update(kw)

        async def __aenter__(self) -> StopClient:
            raise RuntimeError("stop after capturing the client options")

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(ef.httpx, "AsyncClient", StopClient)
    import asyncio

    with pytest.raises(RuntimeError, match="stop after capturing"):
        asyncio.run(
            ef.RemoteClassifier("https://svc.example", timeout_s=ef.INGEST_TIMEOUT_S).classify(
                ["t"]
            )
        )
    assert seen["timeout"] == ef.INGEST_TIMEOUT_S
