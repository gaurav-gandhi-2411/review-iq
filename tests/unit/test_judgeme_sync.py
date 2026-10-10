"""Pre-registered metric tests M1-M6 (docs/specs/judgeme-ingestion.md section 5).

SYNTHETIC / CONTRACT-FIXTURE BASED: a fake Judge.me server (BELIEVED shape) and an in-memory
pipeline. Passing here is not a measurement against a real Judge.me store (spec section 9).
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta

import pytest
from app.core.ingestion.judgeme_client import (
    AuthRejected,
    JudgeMeClient,
    MalformedPage,
    RateLimited,
    Unavailable,
)
from app.core.ingestion.judgeme_source import map_item
from app.core.ingestion.judgeme_sync import RunResult, SyncMeta, SyncSettings, run_sync

from tests.support.judgeme_fake import EPOCH, SHOP, TOKEN, FakeJudgeMe, make_review
from tests.support.judgeme_memory import MemPipeline

NOW = EPOCH + timedelta(days=2)
ORDERS = ["unknown", "newest_first", "oldest_first"]


async def _nosleep(_: float) -> None:
    return None


async def tick(
    srv: FakeJudgeMe,
    pipe: MemPipeline,
    meta: SyncMeta,
    st: SyncSettings,
    now: datetime = NOW,
    per_page: int = 100,
) -> RunResult:
    client = JudgeMeClient(
        SHOP, TOKEN, transport=srv.transport(), sleep=_nosleep, rng=lambda: 0.0, per_page=per_page
    )
    res = await run_sync(client, pipe, pipe, pipe, meta, st, now)
    pipe.drain()
    return res


def visible_hashes(srv: FakeJudgeMe) -> set[str]:
    out = set()
    for r in srv.reviews:
        m = map_item(r)
        if not isinstance(m, str) and m.visible:
            out.add(m.input_hash)
    return out


def source(n: int, start: int = 1) -> list[dict]:
    return [make_review(i) for i in range(start, start + n)]


# ---- M1 completeness + M2 idempotency ---------------------------------------------------------


@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("n", [0, 1, 99, 100, 101, 200, 250])
async def test_m1_completeness_and_m2_idempotency(n: int, order: str) -> None:
    srv = FakeJudgeMe(
        source(n), order="oldest_first" if order == "oldest_first" else "newest_first"
    )
    pipe, meta = MemPipeline(), SyncMeta()
    st = SyncSettings(order=order)  # type: ignore[arg-type]
    for run in range(6):  # 1 ingest + 5 consecutive re-runs
        res = await tick(srv, pipe, meta, st, NOW + timedelta(hours=6 * run))
        meta = res.meta
        if run == 0:
            assert set(pipe.extractions) == visible_hashes(srv)  # M1: 100 percent
            assert len(pipe.staged_ids()) == n
        else:
            assert res.plan.stage == [] and res.plan.counts["new"] == 0  # M2: nothing re-staged
    assert len(pipe.staged_ids()) == len(set(pipe.staged_ids())) == n  # 0 duplicates staged
    assert pipe.conflicts == 0


async def test_m2_crash_between_stage_and_state_restages_but_never_duplicates() -> None:
    srv = FakeJudgeMe(source(30))
    pipe, st = MemPipeline(), SyncSettings()
    pipe.crash_on_save_state = True
    with pytest.raises(RuntimeError):
        await tick(srv, pipe, SyncMeta(), st)
    pipe.drain()
    pipe.crash_on_save_state = False
    await tick(srv, pipe, SyncMeta(), st, NOW + timedelta(hours=6))
    assert set(pipe.extractions) == visible_hashes(srv) and len(pipe.extractions) == 30
    assert pipe.conflicts == 30  # re-staged texts hit the extractions cache, no second row


# ---- M3a edits and deletions ------------------------------------------------------------------


async def test_m3a_edit_unpublish_hide_delete_republish_unknown_order() -> None:
    srv = FakeJudgeMe(source(10))
    pipe, st, meta = MemPipeline(), SyncSettings(), SyncMeta()
    meta = (await tick(srv, pipe, meta, st)).meta
    later = NOW + timedelta(hours=6)
    srv.edit(3, later, body="Changed my mind, it broke")  # edit
    srv.edit(4, later, published=False)  # unpublish
    srv.edit(5, later, hidden=True)  # hide
    srv.delete(6)  # delete (absence)
    res = await tick(srv, pipe, meta, st, later)
    meta = res.meta
    assert res.plan.counts["edited"] == 1 and res.plan.counts["removed_unpublished"] == 2
    assert set(pipe.extractions) == visible_hashes(srv) | {map_item(make_review(6)).input_hash}  # type: ignore[union-attr]
    # absence needs a second consecutive complete scan (strike rule): lag 2T
    res = await tick(srv, pipe, meta, st, later + timedelta(hours=6))
    meta = res.meta
    assert set(pipe.extractions) == visible_hashes(srv)  # 100 percent after 2 scans
    srv.edit(4, later + timedelta(hours=12), published=True)  # republish -> NEW again
    await tick(srv, pipe, meta, st, later + timedelta(hours=12))
    assert set(pipe.extractions) == visible_hashes(srv) and len(pipe.extractions) == 8


async def test_identical_text_reviews_share_one_extraction_until_both_gone() -> None:
    srv = FakeJudgeMe([make_review(1, body="same"), make_review(2, body="same", title="Nice")])
    pipe, st, meta = MemPipeline(), SyncSettings(), SyncMeta()
    meta = (await tick(srv, pipe, meta, st)).meta
    assert len(pipe.extractions) == 1
    srv.edit(1, NOW + timedelta(hours=1), published=False)
    meta = (await tick(srv, pipe, meta, st, NOW + timedelta(hours=1))).meta
    assert len(pipe.extractions) == 1  # review 2 still references it
    srv.edit(2, NOW + timedelta(hours=2), published=False)
    await tick(srv, pipe, meta, st, NOW + timedelta(hours=2))
    assert pipe.extractions == {}


async def test_m3a_known_order_edit_lag_bounded_by_reconcile_interval() -> None:
    """Old review edited: invisible to head-only incrementals, caught by the next full scan."""
    T, R = timedelta(hours=6), timedelta(hours=24)
    old = [make_review(i, created=EPOCH + timedelta(minutes=i)) for i in range(1, 501)]
    srv = FakeJudgeMe(old, order="newest_first")
    pipe = MemPipeline()
    # overlap < data span, otherwise the early-stop never triggers and this is a full scan
    st = SyncSettings(order="newest_first", reconcile_interval=R, overlap=timedelta(hours=1))
    now = EPOCH + timedelta(days=30)
    meta = (await tick(srv, pipe, SyncMeta(), st, now, per_page=10)).meta
    edited_at = now + timedelta(hours=1)
    srv.edit(2, edited_at, body="edited long after posting")  # old review, order key = created_at
    reflected_at = None
    for k in range(1, 12):
        t = now + k * T
        res = await tick(srv, pipe, meta, st, t, per_page=10)
        meta = res.meta
        if map_item(srv.get(2)).input_hash in pipe.extractions:  # type: ignore[union-attr]
            reflected_at = t
            assert res.mode == "full"  # head-only incrementals really could not see it
            break
    assert reflected_at is not None
    assert reflected_at - edited_at <= R + T  # stated bound (spec 3.7)


async def test_m3a_deletion_lag_bound_known_order() -> None:
    T, R = timedelta(hours=6), timedelta(hours=24)
    srv = FakeJudgeMe(source(300), order="newest_first")
    pipe, st = MemPipeline(), SyncSettings(order="newest_first", reconcile_interval=R)
    now = EPOCH + timedelta(days=1)
    meta = (await tick(srv, pipe, SyncMeta(), st, now, per_page=10)).meta
    srv.delete(7)
    gone_hash = map_item(make_review(7)).input_hash  # type: ignore[union-attr]
    for k in range(1, 40):
        t = now + k * T
        meta = (await tick(srv, pipe, meta, st, t, per_page=10)).meta
        if gone_hash not in pipe.extractions:
            assert t - now <= 2 * R + T  # 2 full scans (strikes) + one tick
            return
    pytest.fail("deletion never reflected")


# ---- M3b false deletions ----------------------------------------------------------------------


async def test_m3b_empty_page_glitch_and_truncated_scan_delete_nothing() -> None:
    srv = FakeJudgeMe(source(40))
    pipe, st, meta = MemPipeline(), SyncSettings(), SyncMeta()
    meta = (await tick(srv, pipe, meta, st)).meta
    before = dict(pipe.state)
    srv.malformed_next(
        '{"current_page": 1, "per_page": 100, "reviews": []}', times=1
    )  # 200 + empty
    res = await tick(srv, pipe, meta, st, NOW + timedelta(hours=6))
    assert res.plan.suspect and res.plan.counts["removed_absent"] == 0
    assert pipe.state == before and len(pipe.extractions) == 40
    srv.fail_next(503, times=99)  # truncated: error mid-run
    with pytest.raises(Unavailable):
        await tick(srv, pipe, meta, st, NOW + timedelta(hours=12))
    assert pipe.state == before and len(pipe.extractions) == 40


async def test_m3b_mid_scan_deletion_shift_is_a_strike_not_a_removal() -> None:
    srv = FakeJudgeMe(source(250), order="newest_first")
    pipe, st, meta = MemPipeline(), SyncSettings(), SyncMeta()
    meta = (await tick(srv, pipe, meta, st)).meta
    # Delete the newest review after page 1 was served: page 2 shifts up by one and review 150
    # is skipped by THIS scan although it is still published.
    base = len(srv.request_log)  # request numbering is cumulative across ticks
    srv.before_request = lambda n, page: srv.delete(250) if n == base + 2 else None
    res = await tick(srv, pipe, meta, st, NOW + timedelta(hours=6))
    srv.before_request = None
    assert pipe.state["150"].missing_strikes == 1 and pipe.state["150"].state == "active"
    assert len(pipe.extractions) == 250  # nothing removed yet
    res = await tick(srv, pipe, res.meta, st, NOW + timedelta(hours=12))
    assert pipe.state["150"].missing_strikes == 0  # seen again: strike cleared
    assert pipe.state["250"].missing_strikes == 1  # genuinely deleted: strike 1
    await tick(srv, pipe, res.meta, st, NOW + timedelta(hours=18))
    assert pipe.state["250"].state == "gone"
    assert [r.review_id for r in pipe.state.values() if r.state == "gone"] == ["250"]
    assert set(pipe.extractions) == visible_hashes(srv)  # 0 false deletions


async def test_m3b_page_cap_incomplete_scan_never_deletes() -> None:
    srv = FakeJudgeMe(source(250))
    pipe, meta = MemPipeline(), SyncMeta()
    meta = (await tick(srv, pipe, meta, SyncSettings())).meta
    capped = SyncSettings(max_pages=1)
    for k in range(1, 4):
        res = await tick(srv, pipe, meta, capped, NOW + timedelta(hours=6 * k))
        assert res.suspect and res.plan.counts["removed_absent"] == 0
    assert len(pipe.extractions) == 250


async def test_m3b_mass_removal_needs_three_strikes() -> None:
    srv = FakeJudgeMe(source(40))
    pipe, st, meta = MemPipeline(), SyncSettings(), SyncMeta()
    meta = (await tick(srv, pipe, meta, st)).meta
    for rid in range(1, 31):  # 75 percent vanish
        srv.delete(rid)
    for k in (1, 2):
        res = await tick(srv, pipe, meta, st, NOW + timedelta(hours=6 * k))
        meta = res.meta
        assert res.plan.suspect and len(pipe.extractions) == 40
    await tick(srv, pipe, meta, st, NOW + timedelta(hours=18))
    assert set(pipe.extractions) == visible_hashes(srv) and len(pipe.extractions) == 10


# ---- M4 freshness (simulated clock) ------------------------------------------------------------


def _p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[max(0, int(0.95 * len(xs)) - 1)]


@pytest.mark.parametrize("order", ORDERS)
async def test_m4_freshness_creates_p95_within_schedule_interval(order: str) -> None:
    T = timedelta(hours=6)
    rng = random.Random(42)
    created = sorted(EPOCH + timedelta(minutes=rng.randrange(14 * 24 * 60)) for _ in range(200))
    srv = FakeJudgeMe([], order="oldest_first" if order == "oldest_first" else "newest_first")
    pipe, meta = MemPipeline(), SyncMeta()
    st = SyncSettings(order=order, backfill_days=365)  # type: ignore[arg-type]
    first_seen: dict[int, datetime] = {}
    modes: Counter[str] = Counter()
    nxt = 0
    t = EPOCH
    while t <= EPOCH + timedelta(days=14, hours=12):
        while nxt < len(created) and created[nxt] <= t:
            srv.add(make_review(nxt + 1, created=created[nxt]))
            nxt += 1
        res = await tick(srv, pipe, meta, st, t, per_page=10)
        meta = res.meta
        modes[res.mode] += 1
        for rid in pipe.state:
            first_seen.setdefault(int(rid), t)
        t += T
    assert len(first_seen) == 200
    if order != "unknown":
        assert modes["incremental"] > 30  # the early-stop path was really exercised
    lags = [(first_seen[i + 1] - created[i]).total_seconds() / 3600 for i in range(200)]
    assert _p95(lags) <= 6.0 and max(lags) <= 6.0  # M4 threshold: p95 and max <= T


async def test_m4_late_publish_inside_overlap_vs_beyond_it() -> None:
    """Moderation delay d: seen within T if d <= W (head pages); beyond W only at a full scan."""
    T, R, W = timedelta(hours=6), timedelta(hours=24), timedelta(hours=72)
    base = [make_review(i, created=EPOCH + timedelta(minutes=i)) for i in range(1, 401)]
    srv = FakeJudgeMe(base, order="newest_first")
    pipe, st = MemPipeline(), SyncSettings(order="newest_first", reconcile_interval=R, overlap=W)
    now = EPOCH + timedelta(days=20)
    t0 = now
    srv.add(make_review(900, created=now - timedelta(hours=12), published=False))
    srv.add(make_review(901, created=now - timedelta(days=6), published=False))
    for i in range(30):  # filler between head and 901: whole pages older than the cutoff
        srv.add(make_review(1000 + i, created=now - timedelta(days=4) + timedelta(minutes=10 * i)))
    meta = (await tick(srv, pipe, SyncMeta(), st, now, per_page=10)).meta
    pub_at = now + timedelta(hours=6)
    srv.edit(900, pub_at, published=True)  # d = 18h <= W
    srv.edit(901, pub_at, published=True)  # d = 6.25 days > W
    seen: dict[int, datetime] = {}
    for k in range(1, 12):
        t = t0 + k * T
        meta = (await tick(srv, pipe, meta, st, t, per_page=10)).meta
        for rid in (900, 901):
            if str(rid) in pipe.state and pipe.state[str(rid)].state == "active":
                seen.setdefault(rid, t)
    assert seen[900] - pub_at <= T
    assert seen[901] - pub_at <= R + T  # the documented miss: only the next full scan
    assert seen[901] - pub_at > T  # and it really was missed by the incrementals


# ---- M5 failure handling leaves no partial writes ----------------------------------------------


@pytest.mark.parametrize(
    ("arrange", "exc"),
    [
        (lambda s: s.fail_next(401), AuthRejected),
        (lambda s: s.revoke_token(), AuthRejected),
        (lambda s: s.fail_next(429, times=99, headers={"Retry-After": "9999"}), RateLimited),
        (lambda s: s.fail_next(429, times=99), RateLimited),
        (lambda s: s.fail_next(500, times=99), Unavailable),
        (lambda s: s.timeout_next(times=99), Unavailable),
        (lambda s: s.malformed_next(times=99), MalformedPage),
    ],
)
async def test_m5_failures_raise_typed_and_write_nothing(arrange, exc) -> None:  # type: ignore[no-untyped-def]
    srv = FakeJudgeMe(source(5))
    pipe, st, meta = MemPipeline(), SyncSettings(), SyncMeta()
    meta = (await tick(srv, pipe, meta, st)).meta
    snapshot = (dict(pipe.state), dict(pipe.extractions), pipe.writes)
    srv.edit(1, NOW + timedelta(hours=1), body="new text that must not be applied")
    arrange(srv)
    with pytest.raises(exc):
        await tick(srv, pipe, meta, st, NOW + timedelta(hours=6))
    assert (dict(pipe.state), dict(pipe.extractions), pipe.writes) == snapshot


async def test_m5_malformed_item_skipped_and_counted_run_completes() -> None:
    srv = FakeJudgeMe(source(5))
    srv.junk = ["junk", {"id": "x"}, None]
    pipe = MemPipeline()
    res = await tick(srv, pipe, SyncMeta(), SyncSettings())
    assert res.plan.counts["malformed"] == 3 and len(pipe.extractions) == 5


# ---- policy: backfill window, enqueue cap, order fallback, privacy -------------------------------


async def test_backfill_window_and_enqueue_cap_defer_not_lose() -> None:
    old = make_review(1, created=NOW - timedelta(days=120))
    srv = FakeJudgeMe([old, *source(10, start=2)])
    pipe, st, meta = MemPipeline(), SyncSettings(max_enqueue=4), SyncMeta()
    res = await tick(srv, pipe, meta, st)
    assert res.plan.counts["skipped_out_of_window"] == 1 and res.plan.counts["deferred"] == 6
    meta = res.meta
    for k in range(1, 4):
        meta = (await tick(srv, pipe, meta, st, NOW + timedelta(hours=6 * k))).meta
    assert len(pipe.extractions) == 10 and "1" not in pipe.state


async def test_order_violation_forces_full_scans_afterwards() -> None:
    srv = FakeJudgeMe(source(30), order_key="id")
    for i, r in enumerate(srv.reviews):
        r["created_at"] = r["updated_at"] = make_review(
            1, created=EPOCH + timedelta(hours=(i * 7) % 11)
        )["created_at"]
    pipe, st = MemPipeline(), SyncSettings(order="newest_first")
    res = await tick(srv, pipe, SyncMeta(), st)
    assert res.meta.order_violation
    res2 = await tick(srv, pipe, res.meta, st, NOW + timedelta(hours=1))
    assert res2.mode == "full"


async def test_m6_no_reviewer_pii_in_staged_rows_or_state() -> None:
    srv = FakeJudgeMe(source(20))
    pipe = MemPipeline()
    await tick(srv, pipe, SyncMeta(), SyncSettings())
    blob = repr(pipe.staged_log) + repr(pipe.state) + repr(pipe.extractions)
    for needle in ("@example.com", "Buyer Name", "+91900000", "buyer1@"):
        assert needle not in blob
