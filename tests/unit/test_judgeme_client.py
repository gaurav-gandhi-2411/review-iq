"""JudgeMeClient contract tests against the fake server (SYNTHETIC; spec M5, M8, SSRF table)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from app.core.ingestion.judgeme_client import (
    AuthRejected,
    ClientRequestError,
    InvalidShopDomain,
    JudgeMeClient,
    MalformedPage,
    RateLimited,
    ShopNotFound,
    Unavailable,
    _RedactTokenFilter,
    validate_shop_domain,
)
from scripts.record_judgeme_fixture import redact

from tests.support.judgeme_fake import SHOP, TOKEN, FakeJudgeMe, make_review


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def client(srv: FakeJudgeMe, sleeps: Sleeps, **kw: object) -> JudgeMeClient:
    return JudgeMeClient(
        SHOP,
        kw.pop("token", TOKEN),
        transport=srv.transport(),
        sleep=sleeps,
        rng=lambda: 0.5,
        **kw,  # type: ignore[arg-type]
    )


HOSTILE = [
    "evil.com",
    "a.myshopify.com.evil.com",
    "x\n.myshopify.com",
    "x.myshopify.com\nHost: evil.com",
    "x.myshopify.com:8080",
    "user@x.myshopify.com",
    "127.0.0.1",
    "-x.myshopify.com",
    ".myshopify.com",
    "",
    "x.myshopify.com/path",
    "x.myshopify.com?a=b",
    "xn--e1afmkfd.myshopify.com‮",
    "a" * 300 + ".myshopify.com",
]


@pytest.mark.parametrize("bad", HOSTILE)
def test_hostile_shop_domain_rejected_before_any_request(bad: str) -> None:
    srv = FakeJudgeMe()
    with pytest.raises(InvalidShopDomain):
        JudgeMeClient(bad, TOKEN, transport=srv.transport())
    assert srv.request_log == []


def test_shop_domain_normalised() -> None:
    assert validate_shop_domain("  Demo-Store.MyShopify.com \n") == SHOP  # edge whitespace only


async def test_success_uses_header_by_default_and_fixed_host() -> None:
    srv = FakeJudgeMe([make_review(1), make_review(2)])
    page = await client(srv, Sleeps()).fetch_page(1)
    assert [r["id"] for r in page.reviews] == [2, 1]
    assert page.echoed_per_page == 100
    req = srv.request_log[0]
    assert req["host"] == "api.judge.me" and req["path"] == "/api/v1/reviews"
    assert req["token_in_header"] and not req["token_in_query"]
    assert "product_id" not in req["params"]  # F3: never pass product_id


async def test_query_transport_sends_query_param_only() -> None:
    srv = FakeJudgeMe([make_review(1)])
    await client(srv, Sleeps(), auth_transport="query").fetch_page(1)
    req = srv.request_log[0]
    assert req["token_in_query"] and not req["token_in_header"]


async def test_bad_token_is_typed_and_token_never_leaks(caplog: pytest.LogCaptureFixture) -> None:
    srv = FakeJudgeMe([make_review(1)])
    caplog.set_level(logging.DEBUG)
    with pytest.raises(AuthRejected) as ei:
        await client(srv, Sleeps(), token="wrong-SECRET-token").fetch_page(1)
    assert ei.value.status == 401 and len(srv.request_log) == 1  # no retry on auth failure
    blob = repr(ei.value) + str(ei.value) + caplog.text
    assert "wrong-SECRET-token" not in blob and TOKEN not in blob


async def test_revoked_token_after_success_is_rejected() -> None:
    srv = FakeJudgeMe([make_review(1)])
    c = client(srv, Sleeps())
    await c.fetch_page(1)
    srv.revoke_token()
    with pytest.raises(AuthRejected):
        await c.fetch_page(1)


async def test_forbidden_is_auth_rejected() -> None:
    srv = FakeJudgeMe()
    srv.fail_next(403)
    with pytest.raises(AuthRejected) as ei:
        await client(srv, Sleeps()).fetch_page(1)
    assert ei.value.status == 403


async def test_unknown_shop_404() -> None:
    srv = FakeJudgeMe(shop="other-store.myshopify.com")
    with pytest.raises(ShopNotFound):
        await client(srv, Sleeps()).fetch_page(1)


async def test_429_with_retry_after_sleeps_that_long_then_succeeds() -> None:
    srv = FakeJudgeMe([make_review(1)])
    srv.fail_next(429, headers={"Retry-After": "7"})
    sleeps = Sleeps()
    page = await client(srv, sleeps).fetch_page(1)
    assert sleeps.calls == [7.0] and len(page.reviews) == 1


async def test_429_without_retry_after_uses_exponential_backoff_with_jitter() -> None:
    srv = FakeJudgeMe([make_review(1)])
    srv.fail_next(429, times=3)
    sleeps = Sleeps()
    await client(srv, sleeps).fetch_page(1)
    # rng fixed at 0.5: full jitter = 0.5 * min(30, 2**attempt) for attempts 1..3
    assert sleeps.calls == [1.0, 2.0, 4.0]


async def test_429_retry_after_over_cap_aborts_without_sleeping() -> None:
    srv = FakeJudgeMe()
    srv.fail_next(429, headers={"Retry-After": "3600"})
    sleeps = Sleeps()
    with pytest.raises(RateLimited) as ei:
        await client(srv, sleeps).fetch_page(1)
    assert ei.value.retry_after == 3600.0 and sleeps.calls == []


async def test_429_exhausts_bounded_attempts() -> None:
    srv = FakeJudgeMe()
    srv.fail_next(429, times=99)
    with pytest.raises(RateLimited):
        await client(srv, Sleeps()).fetch_page(1)
    assert len(srv.request_log) == 4


async def test_5xx_retried_then_succeeds() -> None:
    srv = FakeJudgeMe([make_review(1)])
    srv.fail_next(503, times=2)
    c = client(srv, Sleeps())
    assert len((await c.fetch_page(1)).reviews) == 1
    assert c.retries == 2


async def test_5xx_exhausted_raises_unavailable_after_bounded_attempts() -> None:
    srv = FakeJudgeMe()
    srv.fail_next(500, times=99)
    with pytest.raises(Unavailable):
        await client(srv, Sleeps()).fetch_page(1)
    assert len(srv.request_log) == 4


async def test_timeout_retried_then_unavailable() -> None:
    srv = FakeJudgeMe()
    srv.timeout_next(times=99)
    with pytest.raises(Unavailable):
        await client(srv, Sleeps()).fetch_page(1)
    assert len(srv.request_log) == 4


async def test_timeout_then_success() -> None:
    srv = FakeJudgeMe([make_review(1)])
    srv.timeout_next(times=1)
    assert len((await client(srv, Sleeps()).fetch_page(1)).reviews) == 1


@pytest.mark.parametrize("text", ["<html>oops</html>", "[]", '{"reviews": "nope"}', '{"x": 1}'])
async def test_malformed_page_retried_then_typed(text: str) -> None:
    srv = FakeJudgeMe()
    srv.malformed_next(text, times=99)
    with pytest.raises(MalformedPage):
        await client(srv, Sleeps()).fetch_page(1)
    assert len(srv.request_log) == 4


async def test_other_4xx_not_retried() -> None:
    srv = FakeJudgeMe()
    srv.fail_next(422)
    with pytest.raises(ClientRequestError):
        await client(srv, Sleeps()).fetch_page(1)
    assert len(srv.request_log) == 1


def test_log_filter_redacts_api_token_in_url_object_and_string() -> None:
    import httpx

    url = httpx.URL("https://x/?api_token=SECRET&page=1")  # httpx logs a URL object, not a str
    rec = logging.LogRecord(
        "httpx", logging.INFO, "", 0, 'HTTP Request: %s %s "200"', ("GET", url), None
    )
    assert _RedactTokenFilter().filter(rec)
    assert "SECRET" not in rec.getMessage() and "page=1" in rec.getMessage()
    rec2 = logging.LogRecord(
        "httpx", logging.INFO, "", 0, "GET https://x/?api_token=SECRET2", (), None
    )
    _RedactTokenFilter().filter(rec2)
    assert "SECRET2" not in rec2.getMessage()


def test_recorder_redacts_reviewer_identity() -> None:
    raw = make_review(5, body="call me on +91 98765 43210 or a@b.com")
    out = json.dumps(redact({"reviews": [raw]}))
    for needle in ("buyer5@example.com", "Buyer Name5", "9000005", "98765", "a@b.com"):
        assert needle not in out


async def test_replay_from_recording(tmp_path: Path) -> None:
    rec = tmp_path / "rec.json"
    rec.write_text(
        json.dumps(
            {
                "requests": [
                    {
                        "page": 1,
                        "per_page": 100,
                        "status": 200,
                        "headers": {},
                        "body": {"current_page": 1, "per_page": 100, "reviews": [make_review(1)]},
                    },
                    {
                        "page": 2,
                        "per_page": 100,
                        "status": 429,
                        "headers": {"Retry-After": "2"},
                        "body": {"error": "slow down"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    srv = FakeJudgeMe.from_recording(rec)
    sleeps = Sleeps()
    c = client(srv, sleeps)
    assert len((await c.fetch_page(1)).reviews) == 1
    with pytest.raises(RateLimited):  # recorded 429 replays every time -> bounded attempts
        await c.fetch_page(2)
    assert sleeps.calls == [2.0, 2.0, 2.0]
