"""Mapping and scan tests (SYNTHETIC / contract-fixture based; spec M1, M6, ordering A-ORDER)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from app.core.ingestion.base import Source, SourceError
from app.core.ingestion.judgeme_client import JudgeMeClient
from app.core.ingestion.judgeme_source import (
    JudgeMeSource,
    Mapped,
    map_item,
    scan_full,
    scan_incremental,
)
from app.core.schemas import ReviewRequest

from tests.support.judgeme_fake import EPOCH, SHOP, TOKEN, FakeJudgeMe, make_review


async def _nosleep(_: float) -> None:
    return None


def mk_client(srv: FakeJudgeMe, per_page: int = 100) -> JudgeMeClient:
    return JudgeMeClient(
        SHOP, TOKEN, transport=srv.transport(), sleep=_nosleep, rng=lambda: 0.0, per_page=per_page
    )


def reviews(n: int, start: int = 1) -> list[dict]:
    return [make_review(i) for i in range(start, start + n)]


# ---- mapping -------------------------------------------------------------------------------


def test_mapper_drops_reviewer_pii_and_matches_cache_key() -> None:
    raw = make_review(7, body="Great fit", title="Love it", rating=4)
    m = map_item(raw)
    assert isinstance(m, Mapped)
    blob = repr(m) + repr(m.to_row())
    for pii in ("buyer7@example.com", "Buyer Name7", "9000007", "+91900000"):
        assert pii not in blob  # M6
    assert m.text == "Love it\nGreat fit" and m.stars == 4.0 and m.product == "Product 111"
    assert m.input_hash == ReviewRequest(text=m.text).input_hash()
    assert m.to_row()["source_review_id"] == "7" and "review_date" in m.to_row()


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("str", "not_an_object"),
        ([], "not_an_object"),
        ({}, "bad_id"),
        ({"id": True}, "bad_id"),
        ({"id": "abc"}, "bad_id"),
        ({"id": None}, "bad_id"),
    ],
)
def test_malformed_items_return_reason(raw: object, reason: str) -> None:
    assert map_item(raw) == reason


def test_visibility_and_edge_fields() -> None:
    assert map_item(make_review(1, published=False)).visible is False  # type: ignore[union-attr]
    assert map_item(make_review(1, hidden=True)).visible is False  # type: ignore[union-attr]
    assert map_item({**make_review(1), "published": "false"}).visible is False  # type: ignore[union-attr]
    empty = map_item(make_review(1, body="  ", title=""))
    assert isinstance(empty, Mapped) and not empty.visible and empty.text == ""
    minimal = map_item({"id": "42", "body": "ok"})
    assert isinstance(minimal, Mapped) and minimal.visible and minimal.created_at is None
    long = map_item(make_review(1, body="x" * 6000))
    assert isinstance(long, Mapped) and len(long.text) == 5000 and long.truncated
    assert (
        map_item(make_review(1, body="a")).content_hash
        != map_item(make_review(1, body="b")).content_hash
    )  # type: ignore[union-attr]


# ---- M1 completeness -------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["newest_first", "oldest_first"])
@pytest.mark.parametrize("echo", [True, False])
@pytest.mark.parametrize("n", [0, 1, 99, 100, 101, 200, 250])
async def test_full_scan_is_complete_for_page_boundaries(n: int, echo: bool, order: str) -> None:
    srv = FakeJudgeMe(reviews(n), order=order, echo_per_page=echo)
    res = await scan_full(mk_client(srv))
    assert res.complete and set(res.items) == {str(i) for i in range(1, n + 1)}
    assert res.pages == len(srv.request_log)


@pytest.mark.parametrize(
    ("n", "echo", "expected"), [(99, True, 1), (100, True, 2), (200, True, 3), (99, False, 2)]
)
async def test_request_counts_at_boundaries(n: int, echo: bool, expected: int) -> None:
    srv = FakeJudgeMe(reviews(n), echo_per_page=echo)
    await scan_full(mk_client(srv))
    assert len(srv.request_log) == expected


async def test_server_that_caps_per_page_below_request_still_complete() -> None:
    srv = FakeJudgeMe(reviews(70))
    orig = srv.handler

    def capped(req):  # type: ignore[no-untyped-def]
        return orig(
            httpx.Request(req.method, req.url.copy_set_param("per_page", "20"), headers=req.headers)
        )

    c = JudgeMeClient(SHOP, TOKEN, transport=httpx.MockTransport(capped), sleep=_nosleep)
    res = await scan_full(c)
    assert res.complete and len(res.items) == 70


async def test_source_grows_between_pages_loses_nothing_that_existed_at_start() -> None:
    srv = FakeJudgeMe(reviews(250))
    at_start = {str(r["id"]) for r in srv.reviews}

    def grow(request_no: int, page: int) -> None:
        if request_no == 2:  # newest_first: new items push everything down one slot
            for i in range(1000, 1007):
                srv.add(make_review(i, created=EPOCH + timedelta(days=30, minutes=i)))

    srv.before_request = grow
    res = await scan_full(mk_client(srv))
    assert res.complete and at_start <= set(res.items)  # duplicates collapsed by id
    assert len(res.items) <= 257


async def test_malformed_items_are_counted_and_do_not_abort_the_scan() -> None:
    srv = FakeJudgeMe(reviews(5))
    srv.junk = ["garbage", {"id": None, "body": "x"}, {"id": "abc"}, 12]
    res = await scan_full(mk_client(srv))
    assert res.complete and len(res.items) == 5
    assert res.malformed == {"not_an_object": 2, "bad_id": 2}


async def test_page_cap_marks_scan_incomplete() -> None:
    srv = FakeJudgeMe(reviews(250))
    res = await scan_full(mk_client(srv), max_pages=2)
    assert not res.complete and len(res.items) == 200


# ---- ordering assumption ----------------------------------------------------------------------


async def test_order_violation_detected_when_configured_order_is_wrong() -> None:
    srv = FakeJudgeMe(reviews(30), order_key="id")  # id order, but created_at scrambled below
    for i, r in enumerate(srv.reviews):
        r["created_at"] = make_review(1, created=EPOCH + timedelta(hours=(i * 7) % 11))[
            "created_at"
        ]
        r["updated_at"] = r["created_at"]
    res = await scan_full(mk_client(srv), order="newest_first")
    assert res.order_violation
    ok = await scan_full(mk_client(FakeJudgeMe(reviews(30))), order="newest_first")
    assert not ok.order_violation
    assert not (await scan_full(mk_client(srv), order="unknown")).order_violation


@pytest.mark.parametrize("order", ["newest_first", "oldest_first"])
async def test_incremental_finds_new_reviews_with_fewer_requests_and_never_completes(
    order: str,
) -> None:
    old = [make_review(i, created=EPOCH + timedelta(minutes=i)) for i in range(1, 3001)]
    srv = FakeJudgeMe(old, order=order)
    watermark = EPOCH + timedelta(minutes=3000)
    for i in (2001, 2002, 2003):
        srv.add(make_review(i, created=watermark + timedelta(hours=i - 2000)))
    srv.request_log.clear()
    res = await scan_incremental(
        mk_client(srv),
        order=order,
        watermark=watermark,
        overlap=timedelta(hours=1),  # type: ignore[arg-type]
    )
    assert {"2001", "2002", "2003"} <= set(res.items)
    assert not res.complete
    # full scan of 3003 reviews = 32 requests; oldest_first pays ~log2(N) probe requests
    assert len(srv.request_log) <= 16
    full_reqs = len((await scan_full(mk_client(FakeJudgeMe(old + [])))).items)
    assert full_reqs == 3000


async def test_incremental_requires_known_order() -> None:
    with pytest.raises(ValueError, match="known list order"):
        await scan_incremental(
            mk_client(FakeJudgeMe()), order="unknown", watermark=EPOCH, overlap=timedelta(hours=1)
        )


# ---- Source protocol ---------------------------------------------------------------------------


async def test_judgeme_source_conforms_and_returns_visible_rows() -> None:
    srv = FakeJudgeMe([*reviews(3), make_review(9, published=False)])
    src = JudgeMeSource(mk_client(srv))
    assert isinstance(src, Source) and src.source_type == "judgeme"
    rows = await src.fetch_reviews()
    assert sorted(r["source_review_id"] for r in rows) == ["1", "2", "3"]
    assert src.source_meta() == {"shop_domain": SHOP, "fetched_count": 3}


async def test_judgeme_source_raises_source_error_on_failure() -> None:
    srv = FakeJudgeMe(reviews(3))
    srv.fail_next(401)
    with pytest.raises(SourceError, match="judgeme_token_rejected"):
        await JudgeMeSource(mk_client(srv)).fetch_reviews()
