"""Judge.me sync engine: scan -> pure plan -> idempotent execute (spec sections 3.4-3.7).

Storage is injected through three Protocols so this module is importable and fully testable with
no network and no database. The Postgres implementations arrive in a later PR; nothing here is
wired to a route or scheduler, and the whole feature stays behind ENABLE_JUDGEME_CONNECTOR.

Safety rules that matter more than throughput:
  * deletion-by-absence only from a COMPLETE full scan, after N consecutive strikes;
  * a scan that returned zero items while reviews are tracked never deletes anything;
  * execute order is stage -> purge -> state, so a crash re-runs the same plan harmlessly
    (the extractions cache makes a re-staged identical text a no-op, a repeated purge is a no-op).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from app.core.ingestion.judgeme_client import JudgeMeClient
from app.core.ingestion.judgeme_source import (
    DEFAULT_MAX_PAGES,
    ListOrder,
    Mapped,
    ScanResult,
    scan_full,
    scan_incremental,
)

_MIN_DT = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SyncSettings:
    order: ListOrder = "unknown"
    overlap: timedelta = timedelta(hours=72)  # W
    reconcile_interval: timedelta = timedelta(hours=24)  # R
    backfill_days: int = 90
    max_enqueue: int = 500
    max_pages: int = DEFAULT_MAX_PAGES
    strikes: int = 2
    strikes_mass: int = 3
    mass_fraction: float = 0.5
    mass_min_tracked: int = 20


@dataclass(frozen=True)
class StateRow:
    review_id: str
    content_hash: str
    input_hash: str
    state: Literal["active", "gone"] = "active"
    missing_strikes: int = 0
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None


@dataclass(frozen=True)
class SyncMeta:
    watermark: datetime | None = None
    last_full_scan_at: datetime | None = None
    order_violation: bool = False


class SyncStore(Protocol):
    async def load_state(self) -> dict[str, StateRow]: ...
    async def save_state(self, rows: list[StateRow]) -> None: ...


class Stager(Protocol):
    async def stage(self, rows: list[Mapped]) -> str:
        """Stage rows on the durable ingest queue; return the job id."""
        ...


class Purger(Protocol):
    async def delete_extractions(self, input_hashes: list[str]) -> int: ...


@dataclass
class Plan:
    stage: list[Mapped] = field(default_factory=list)
    upserts: dict[str, StateRow] = field(default_factory=dict)
    purge: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    suspect: bool = False


@dataclass
class RunResult:
    mode: Literal["full", "incremental"]
    plan: Plan
    meta: SyncMeta
    job_id: str | None
    scan_complete: bool
    pages: int

    @property
    def suspect(self) -> bool:
        return self.plan.suspect or (self.mode == "full" and not self.scan_complete)


def _row(m: Mapped) -> StateRow:
    return StateRow(
        m.review_id, m.content_hash, m.input_hash, "active", 0, m.created_at, m.updated_at
    )


def make_plan(
    scan: ScanResult, state: dict[str, StateRow], now: datetime, st: SyncSettings
) -> Plan:
    plan = Plan()
    c = plan.counts
    purge: set[str] = set()
    window_start = now - timedelta(days=st.backfill_days)

    def retire(prev: StateRow) -> None:
        plan.upserts[prev.review_id] = replace(prev, state="gone", missing_strikes=0)
        purge.add(prev.input_hash)

    # Newest first so the enqueue cap defers the oldest, least valuable reviews.
    for m in sorted(scan.items.values(), key=lambda x: x.created_at or _MIN_DT, reverse=True):
        prev = state.get(m.review_id)
        active = prev is not None and prev.state == "active"
        if not m.visible:
            if active and prev is not None:
                retire(prev)
                c["removed_unpublished"] += 1
            else:
                c["skipped_unpublished"] += 1
            continue
        if active and prev is not None and prev.content_hash == m.content_hash:
            c["unchanged"] += 1
            if prev.missing_strikes:
                plan.upserts[m.review_id] = replace(prev, missing_strikes=0)
            continue
        if prev is None and m.created_at is not None and m.created_at < window_start:
            c["skipped_out_of_window"] += 1
            continue
        needs_stage = not (active and prev is not None and prev.input_hash == m.input_hash)
        if needs_stage and len(plan.stage) >= st.max_enqueue:
            c["deferred"] += 1  # not recorded in state, so the next run picks it up again
            continue
        if needs_stage:
            plan.stage.append(m)
        if active and prev is not None and needs_stage:
            purge.add(prev.input_hash)
        plan.upserts[m.review_id] = _row(m)
        c["edited" if active else "new"] += 1

    if scan.kind == "full" and scan.complete:
        tracked = [r for r in state.values() if r.state == "active"]
        missing = [r for r in tracked if r.review_id not in scan.items]
        if missing and not scan.items:
            plan.suspect = True  # empty answer while we track reviews: never a mass delete
            c["suspect_empty_scan"] += 1
        elif missing:
            mass = (
                len(tracked) >= st.mass_min_tracked
                and len(missing) / len(tracked) > st.mass_fraction
            )
            plan.suspect = plan.suspect or mass
            need = st.strikes_mass if mass else st.strikes
            for r in missing:
                if r.missing_strikes + 1 >= need:
                    retire(r)
                    c["removed_absent"] += 1
                else:
                    plan.upserts[r.review_id] = replace(r, missing_strikes=r.missing_strikes + 1)
                    c["strike"] += 1

    final = {**state, **plan.upserts}
    refs = Counter(r.input_hash for r in final.values() if r.state == "active")
    plan.purge = sorted(h for h in purge if h and refs[h] == 0)
    c["malformed"] = sum(scan.malformed.values())
    c["truncated"] = scan.truncated
    return plan


def choose_mode(meta: SyncMeta, st: SyncSettings, now: datetime) -> tuple[bool, ListOrder]:
    order: ListOrder = "unknown" if meta.order_violation else st.order
    full = (
        order == "unknown"
        or meta.watermark is None
        or meta.last_full_scan_at is None
        or now - meta.last_full_scan_at >= st.reconcile_interval
    )
    return full, order


async def run_sync(
    client: JudgeMeClient,
    store: SyncStore,
    stager: Stager,
    purger: Purger,
    meta: SyncMeta,
    st: SyncSettings,
    now: datetime,
) -> RunResult:
    """One sync run. Any JudgeMeError propagates BEFORE anything is written."""
    full, order = choose_mode(meta, st, now)
    if full:
        scan = await scan_full(client, order=order, max_pages=st.max_pages)
    else:
        assert meta.watermark is not None  # choose_mode guarantees it for incrementals
        scan = await scan_incremental(
            client,
            order=order,
            watermark=meta.watermark,
            overlap=st.overlap,
            max_pages=st.max_pages,
        )
    plan = make_plan(scan, await store.load_state(), now, st)

    job_id = await stager.stage(plan.stage) if plan.stage else None
    if plan.purge:
        plan.counts["purged"] = await purger.delete_extractions(plan.purge)
    if plan.upserts:
        await store.save_state(list(plan.upserts.values()))
    plan.counts["staged"] = len(plan.stage)

    seen = [m.activity for m in scan.items.values() if m.activity is not None]
    advance = scan.kind == "incremental" or scan.complete
    new_meta = SyncMeta(
        watermark=max([meta.watermark or _MIN_DT, *seen]) if advance and seen else meta.watermark,
        last_full_scan_at=now if full and scan.complete else meta.last_full_scan_at,
        order_violation=meta.order_violation or scan.order_violation,
    )
    return RunResult(scan.kind, plan, new_meta, job_id, scan.complete, scan.pages)
