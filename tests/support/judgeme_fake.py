"""Fake Judge.me server for contract tests (httpx.MockTransport; no network, no socket).

SYNTHETIC / CONTRACT-FIXTURE BASED: the response shape is the BELIEVED one from
docs/specs/judgeme-ingestion.md F7/F8 (API Evangelist profile + help article 8409180). Nothing
here has been compared with a real Judge.me store. It exists so the connector can be tested for
pagination, edits, deletions, 429, 5xx, timeouts, malformed payloads and a revoked token, and so
recorded real responses (scripts/record_judgeme_fixture.py) can be replayed through the same
client via ``FakeJudgeMe.from_recording``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

SHOP = "demo-store.myshopify.com"
TOKEN = "jm_private_FAKE_TOKEN_0123456789"
EPOCH = datetime(2026, 9, 1, tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_review(
    rid: int,
    *,
    created: datetime | None = None,
    updated: datetime | None = None,
    body: str | None = None,
    title: str = "Nice",
    rating: int = 5,
    published: bool = True,
    hidden: bool = False,
    product: int = 111,
) -> dict[str, Any]:
    """A review in the BELIEVED Judge.me shape, with synthetic PII in the reviewer object."""
    created = created or EPOCH + timedelta(minutes=rid)
    return {
        "id": rid,
        "title": title,
        "body": body if body is not None else f"Review body number {rid}, works well",
        "rating": rating,
        "product_external_id": product,
        "product_title": f"Product {product}",
        "reviewer": {
            "id": 9000 + rid,
            "external_id": 7000 + rid,
            "email": f"buyer{rid}@example.com",
            "name": f"Buyer Name{rid}",
            "phone": f"+91900000{rid:04d}",
        },
        "source": "web",
        "curated": "ok",
        "published": published,
        "hidden": hidden,
        "verified": "nothing",
        "created_at": iso(created),
        "updated_at": iso(updated or created),
        "pictures": [],
    }


class FakeJudgeMe:
    """In-memory Judge.me. ``transport()`` returns a MockTransport for JudgeMeClient."""

    def __init__(
        self,
        reviews: list[dict[str, Any]] | None = None,
        *,
        order: str = "newest_first",  # newest_first | oldest_first (by order_key, then id)
        order_key: str = "created_at",  # created_at | updated_at
        echo_per_page: bool = True,
        list_unpublished: bool = True,
        accept_header_token: bool = True,
        token: str = TOKEN,
        shop: str = SHOP,
    ) -> None:
        self.reviews: list[dict[str, Any]] = list(reviews or [])
        self.order = order
        self.order_key = order_key
        self.echo_per_page = echo_per_page
        self.list_unpublished = list_unpublished
        self.accept_header_token = accept_header_token
        self.junk: list[Any] = []  # raw (possibly malformed) items prepended to page 1
        self.token = token
        self.shop = shop
        self.request_log: list[dict[str, Any]] = []
        self._faults: list[tuple[int, httpx.Response | Exception]] = []  # (remaining, what)
        self.before_request: Callable[[int, int], None] | None = None  # (request_no, page)
        self._replay: dict[tuple[int, int], list[dict[str, Any]]] | None = None

    # ---- source mutation helpers -------------------------------------------------------
    def add(self, review: dict[str, Any]) -> None:
        self.reviews.append(review)

    def get(self, rid: int) -> dict[str, Any]:
        return next(r for r in self.reviews if r["id"] == rid)

    def edit(self, rid: int, now: datetime, **fields: Any) -> None:
        r = self.get(rid)
        r.update(fields)
        r["updated_at"] = iso(now)

    def delete(self, rid: int) -> None:
        self.reviews = [r for r in self.reviews if r["id"] != rid]

    def revoke_token(self) -> None:
        self.token = "rotated-by-support"

    # ---- fault injection ------------------------------------------------------------------
    def fail_next(
        self, status: int, times: int = 1, headers: dict[str, str] | None = None, body: Any = None
    ) -> None:
        resp = httpx.Response(status, headers=headers or {}, json=body or {"error": "x"})
        self._faults.append((times, resp))

    def timeout_next(self, times: int = 1) -> None:
        self._faults.append((times, httpx.ReadTimeout("simulated")))

    def malformed_next(self, text: str = "<html>oops</html>", times: int = 1) -> None:
        self._faults.append((times, httpx.Response(200, text=text)))

    # ---- serving ---------------------------------------------------------------------------
    def _visible(self) -> list[dict[str, Any]]:
        rows = [
            r
            for r in self.reviews
            if self.list_unpublished or (r.get("published") and not r.get("hidden"))
        ]
        rows.sort(key=lambda r: (r[self.order_key], r["id"]), reverse=self.order == "newest_first")
        return rows

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params
        page, per_page = int(q.get("page", "1")), int(q.get("per_page", "20"))
        self.request_log.append(
            {
                "host": request.url.host,
                "path": request.url.path,
                "page": page,
                "per_page": per_page,
                "token_in_query": "api_token" in q,
                "token_in_header": "x-api-token" in request.headers,
                "params": dict(q),
            }
        )
        if self.before_request:
            self.before_request(len(self.request_log), page)
        if self._faults:
            remaining, what = self._faults[0]
            if remaining <= 1:
                self._faults.pop(0)
            else:
                self._faults[0] = (remaining - 1, what)
            if isinstance(what, Exception):
                raise what
            return what
        if self._replay is not None:
            return self._serve_replay(page, per_page)
        supplied = q.get("api_token") or (
            request.headers.get("x-api-token") if self.accept_header_token else None
        )
        if supplied != self.token:
            return httpx.Response(401, json={"error": "Invalid token"})
        if q.get("shop_domain") != self.shop:
            return httpx.Response(404, json={"error": "Shop not found"})
        per_page = max(1, min(per_page, 100))
        chunk = self._visible()[(page - 1) * per_page : page * per_page]
        chunk = [*self.junk, *chunk] if page == 1 else chunk
        body: dict[str, Any] = {"current_page": page, "reviews": chunk}
        if self.echo_per_page:
            body["per_page"] = per_page
        return httpx.Response(200, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # ---- record / replay (D1d) ---------------------------------------------------------------
    @classmethod
    def from_recording(cls, path: Path, **kwargs: Any) -> FakeJudgeMe:
        """Replay a file written by scripts/record_judgeme_fixture.py.

        Format: {"requests": [{"page": 1, "per_page": 100, "status": 200, "headers": {},
        "body": {...}}, ...]}. A (page, per_page) with no recording returns an empty page.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        srv = cls(**kwargs)
        srv._replay = {}
        for entry in data["requests"]:
            srv._replay.setdefault((entry["page"], entry["per_page"]), []).append(entry)
        return srv

    def _serve_replay(self, page: int, per_page: int) -> httpx.Response:
        assert self._replay is not None
        entries = self._replay.get((page, per_page))
        if not entries:
            return httpx.Response(
                200, json={"current_page": page, "per_page": per_page, "reviews": []}
            )
        entry = entries.pop(0) if len(entries) > 1 else entries[0]
        return httpx.Response(entry["status"], headers=entry.get("headers", {}), json=entry["body"])
