"""Judge.me review mapping (raw API item -> Mapped) and the scan strategies.

Mapping is the privacy boundary: the ``reviewer`` object (id, email, name, phone), pictures and
everything else outside ``Mapped`` is dropped here and never reaches the queue, the state table
or a log line (spec section 3.2). Field names are the BELIEVED Judge.me shape (spec F7); every
field except ``id`` is optional and absence is tolerated.

Scans (spec section 3.4) return what the source showed plus whether the scan is COMPLETE; only a
complete full scan may ever lead to a deletion-by-absence.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.core.ingestion.base import ReviewRow, SourceError
from app.core.ingestion.judgeme_client import JudgeMeClient, JudgeMeError, Page
from app.core.schemas import ReviewRequest

MAX_TEXT_CHARS = 5000  # ReviewRequest.text max_length
DEFAULT_MAX_PAGES = 500
ListOrder = Literal["unknown", "newest_first", "oldest_first"]


@dataclass(frozen=True)
class Mapped:
    review_id: str
    text: str  # "" when not visible
    product: str | None
    stars: float | None
    created_at: datetime | None
    updated_at: datetime | None
    visible: bool  # published, not hidden, non-empty text
    content_hash: str
    input_hash: str  # matches ReviewRequest.input_hash(), the extractions cache key
    truncated: bool = False

    @property
    def activity(self) -> datetime | None:
        known = [d for d in (self.created_at, self.updated_at) if d is not None]
        return max(known) if known else None

    def to_row(self) -> ReviewRow:
        row: ReviewRow = {"text": self.text, "source_review_id": self.review_id}
        if self.product:
            row["product"] = self.product
        if self.stars is not None:
            row["stars"] = self.stars
        if self.created_at:
            row["review_date"] = self.created_at.isoformat()
        return row


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is(value: Any, target: bool) -> bool:
    """True iff value is the boolean `target` (also accepts "true"/"false" strings)."""
    if isinstance(value, str):
        value = {"true": True, "false": False}.get(value.strip().lower())
    return value is target


def map_item(raw: Any) -> Mapped | str:
    """Map one raw item, or return a short reason string (malformed: counted, skipped)."""
    if not isinstance(raw, dict):
        return "not_an_object"
    rid = raw.get("id")
    if isinstance(rid, bool) or not (
        isinstance(rid, int) or (isinstance(rid, str) and rid.isdigit())
    ):
        return "bad_id"
    raw_title, raw_body = raw.get("title"), raw.get("body")
    title = raw_title if isinstance(raw_title, str) else ""
    body = raw_body if isinstance(raw_body, str) else ""
    text = "\n".join(p for p in (title.strip(), body.strip()) if p)
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS].strip()
    rating = raw.get("rating")
    stars = float(rating) if isinstance(rating, (int, float)) and 1 <= rating <= 5 else None
    product = next(
        (
            str(raw[k])
            for k in ("product_title", "product_handle", "product_external_id")
            if raw.get(k)
        ),
        None,
    )
    published = not _is(raw.get("published", True), False) and not _is(raw.get("hidden"), True)
    visible = published and bool(text)
    canonical = json.dumps(
        [title, body, rating, product, published], ensure_ascii=False, separators=(",", ":")
    )
    return Mapped(
        review_id=str(rid),
        text=text if visible else "",
        product=product,
        stars=stars,
        created_at=_dt(raw.get("created_at")),
        updated_at=_dt(raw.get("updated_at")),
        visible=visible,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        input_hash=ReviewRequest(text=text).input_hash() if visible else "",
        truncated=truncated and visible,
    )


@dataclass
class ScanResult:
    kind: Literal["full", "incremental"]
    items: dict[str, Mapped] = field(default_factory=dict)
    complete: bool = False
    order_violation: bool = False
    pages: int = 0
    malformed: Counter[str] = field(default_factory=Counter)
    truncated: int = 0


def _absorb(result: ScanResult, page: Page) -> list[Mapped]:
    mapped: list[Mapped] = []
    for raw in page.reviews:
        m = map_item(raw)
        if isinstance(m, str):
            result.malformed[m] += 1
            continue
        result.truncated += m.truncated
        prev = result.items.get(m.review_id)
        # Page shifts can show an item twice; keep the most recently updated copy.
        if prev is None or (m.updated_at or datetime.min.replace(tzinfo=UTC)) >= (
            prev.updated_at or datetime.min.replace(tzinfo=UTC)
        ):
            result.items[m.review_id] = m
        mapped.append(m)
    result.pages += 1
    return mapped


def _is_end(page: Page, per_page: int) -> bool:
    """Terminal rule (spec 3.3): empty page, or short page whose per_page echo matches."""
    return not page.reviews or (len(page.reviews) < per_page and page.echoed_per_page == per_page)


def _monotone(seq: list[datetime], newest_first: bool) -> bool:
    pairs = zip(seq, seq[1:], strict=False)
    return all((a >= b) if newest_first else (a <= b) for a, b in pairs)


def _order_ok(raw_pages: list[list[Mapped]], order: ListOrder) -> bool:
    if order == "unknown":
        return True
    flat = [m for page in raw_pages for m in page]
    newest = order == "newest_first"
    for key in ("created_at", "updated_at"):
        seq = [getattr(m, key) for m in flat if getattr(m, key) is not None]
        if len(seq) == len(flat) and _monotone(seq, newest):
            return True
    return len(flat) < 2


async def scan_full(
    client: JudgeMeClient, *, order: ListOrder = "unknown", max_pages: int = DEFAULT_MAX_PAGES
) -> ScanResult:
    res = ScanResult("full")
    seen_pages: list[list[Mapped]] = []
    for page_no in range(1, max_pages + 1):
        page = await client.fetch_page(page_no)
        seen_pages.append(_absorb(res, page))
        if _is_end(page, client.per_page):
            res.complete = True
            break
    res.order_violation = not _order_ok(seen_pages, order)
    return res


def _all_older(mapped: list[Mapped], cutoff: datetime) -> bool:
    acts = [m.activity for m in mapped]
    return bool(acts) and all(a is not None and a < cutoff for a in acts)


async def scan_incremental(
    client: JudgeMeClient,
    *,
    order: ListOrder,
    watermark: datetime,
    overlap: timedelta,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> ScanResult:
    """Early-stop scan. Never ``complete``: it can add/update but never cause a deletion."""
    if order == "unknown":
        raise ValueError("incremental scan requires a known list order")
    res = ScanResult("incremental")
    cutoff = watermark - overlap
    if order == "newest_first":
        for page_no in range(1, max_pages + 1):
            page = await client.fetch_page(page_no)
            mapped = _absorb(res, page)
            if _is_end(page, client.per_page) or _all_older(mapped, cutoff):
                break
        return res
    # oldest_first: locate the tail (exponential then binary search), walk back, read forward.
    per = client.per_page
    hi = 1
    while hi < max_pages and len((await client.fetch_page(hi)).reviews) >= per:
        hi *= 2
    lo = hi // 2 + 1 if hi > 1 else 1
    while lo < hi:  # first page with fewer than per_page items in [lo, hi]
        mid = (lo + hi) // 2
        if len((await client.fetch_page(mid)).reviews) >= per:
            lo = mid + 1
        else:
            hi = mid
    last = max(1, min(hi, max_pages))
    start = last
    while True:
        page = await client.fetch_page(start)
        mapped = _absorb(res, page)
        if start == 1 or _all_older(mapped, cutoff):
            break
        start -= 1
    for page_no in range(last + 1, max_pages + 1):
        page = await client.fetch_page(page_no)
        _absorb(res, page)
        if _is_end(page, per):
            break
    return res


class JudgeMeSource:
    """Full pull as a ``Source`` (importable, network-free to construct)."""

    def __init__(self, client: JudgeMeClient, *, order: ListOrder = "unknown") -> None:
        self._client = client
        self._order = order
        self._count = 0

    @property
    def source_type(self) -> str:
        return "judgeme"

    async def fetch_reviews(self) -> list[ReviewRow]:
        try:
            res = await scan_full(self._client, order=self._order)
        except JudgeMeError as exc:
            raise SourceError(f"{exc.code}: {exc}") from exc
        if not res.complete:
            raise SourceError("judgeme_scan_incomplete: page cap reached before end of data")
        rows = [m.to_row() for m in res.items.values() if m.visible]
        self._count = len(rows)
        return rows

    def source_meta(self) -> dict[str, object]:
        return {"shop_domain": self._client.shop_domain, "fetched_count": self._count}
