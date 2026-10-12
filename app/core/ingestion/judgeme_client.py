"""Judge.me REST client: one page of reviews per call, with the failure contract of
docs/specs/judgeme-ingestion.md section 3.3 / 3.5.

Everything here is contract-fixture tested (tests/support/judgeme_fake.py); no real Judge.me
store has been called. Response shapes marked BELIEVED in the spec are parsed defensively.

Secret hygiene: the token is only ever placed in a request header or query param, never in an
exception message, never in a log line. We deliberately do not use ``raise_for_status`` (its
message embeds the full URL, which carries ``api_token=`` on the query transport).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx

JUDGEME_BASE_URL = "https://api.judge.me/api/v1"
MAX_PER_PAGE = 100  # VERIFIED: help article 8409180
MAX_ATTEMPTS = 4
BACKOFF_CAP_SECONDS = 30.0
MAX_RETRY_AFTER_SECONDS = 120.0  # longer than this: abort the run and defer, don't sleep
_RETRYABLE_5XX = frozenset({500, 502, 503, 504})
# fullmatch, not "$": "$" accepts a trailing newline (SSRF / header-injection shape).
_SHOP_RE = re.compile(r"[a-z0-9][a-z0-9-]*\.myshopify\.com")
_TOKEN_IN_URL_RE = re.compile(r"(api_token=)[^&\s\"']+")

AuthTransport = Literal["header", "query"]


class JudgeMeError(Exception):
    """Base class. ``code`` is a stable machine code surfaced to the merchant."""

    code = "judgeme_error"


class InvalidShopDomain(JudgeMeError, ValueError):
    code = "invalid_shop_domain"


class AuthRejected(JudgeMeError):
    """401/403: bad, revoked or plan-ineligible token (Judge.me does not say which: F15)."""

    code = "judgeme_token_rejected"

    def __init__(self, status: int) -> None:
        super().__init__(f"Judge.me rejected the token (HTTP {status})")
        self.status = status


class ShopNotFound(JudgeMeError):
    code = "judgeme_shop_not_found"


class RateLimited(JudgeMeError):
    code = "judgeme_rate_limited"

    def __init__(self, retry_after: float | None) -> None:
        super().__init__("Judge.me rate limit; retry later")
        self.retry_after = retry_after


class Unavailable(JudgeMeError):
    """5xx, timeout or transport failure after bounded retries."""

    code = "judgeme_unreachable"


class MalformedPage(JudgeMeError):
    code = "judgeme_malformed_response"


class ClientRequestError(JudgeMeError):
    """Other 4xx: our request is wrong; retrying cannot help."""

    code = "judgeme_bad_request"


def validate_shop_domain(shop_domain: str) -> str:
    """Normalise and validate; raise InvalidShopDomain. Call BEFORE any outbound request."""
    s = shop_domain.strip().lower() if isinstance(shop_domain, str) else ""
    if len(s) > 255 or not _SHOP_RE.fullmatch(s):
        raise InvalidShopDomain("shop_domain must look like store-name.myshopify.com")
    return s


class _RedactTokenFilter(logging.Filter):
    """Strip ``api_token=...`` from httpx's INFO request logs (query transport)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN_IN_URL_RE.sub(r"\1[REDACTED]", str(record.msg))
        if isinstance(record.args, tuple):
            # httpx logs the URL as an httpx.URL object, not a str: stringify before matching.
            record.args = tuple(
                _TOKEN_IN_URL_RE.sub(r"\1[REDACTED]", str(a)) if "api_token=" in str(a) else a
                for a in record.args
            )
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_RedactTokenFilter())


@dataclass(frozen=True)
class Page:
    page: int
    reviews: list[Any]  # raw items; per-item validation is the mapper's job
    echoed_per_page: int | None  # None when the response does not echo it (F8 BELIEVED)


@dataclass(frozen=True)
class _Retry:
    error: JudgeMeError
    delay: float | None  # None: use the default exponential back-off with jitter


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _parse_page(resp: httpx.Response) -> Page | _Retry:
    try:
        body = resp.json()
    except ValueError:
        return _Retry(MalformedPage("Judge.me response is not JSON"), None)
    if not isinstance(body, dict) or not isinstance(body.get("reviews"), list):
        return _Retry(MalformedPage("Judge.me response has no 'reviews' list"), None)
    echoed = body.get("per_page")
    current = body.get("current_page")
    return Page(
        page=current if isinstance(current, int) else 0,
        reviews=body["reviews"],
        echoed_per_page=echoed
        if isinstance(echoed, int) and not isinstance(echoed, bool)
        else None,
    )


class JudgeMeClient:
    def __init__(
        self,
        shop_domain: str,
        api_token: str,
        *,
        auth_transport: AuthTransport = "header",
        per_page: int = MAX_PER_PAGE,
        base_url: str = JUDGEME_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._shop = validate_shop_domain(shop_domain)  # raises before any request is possible
        self._token = api_token
        self._auth_transport = auth_transport
        self.per_page = max(1, min(per_page, MAX_PER_PAGE))
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self._rng = rng
        self._max_attempts = max_attempts
        self.requests_made = 0
        self.retries = 0

    @property
    def shop_domain(self) -> str:
        return self._shop

    def _request_parts(self, page: int) -> tuple[dict[str, str], dict[str, str | int]]:
        params: dict[str, str | int] = {
            "shop_domain": self._shop,
            "per_page": self.per_page,
            "page": page,
        }
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._auth_transport == "header":
            headers["X-Api-Token"] = self._token
        else:
            params["api_token"] = self._token
        return headers, params

    def _backoff(self, attempt: int) -> float:
        return float(self._rng()) * min(BACKOFF_CAP_SECONDS, 2.0**attempt)  # full jitter

    def _classify(self, resp: httpx.Response) -> Page | _Retry:
        """A Page, a retryable condition, or raise for a non-retryable one."""
        status = resp.status_code
        if status == 200:
            return _parse_page(resp)
        if status in (401, 403):
            raise AuthRejected(status)
        if status == 404:
            raise ShopNotFound("Judge.me does not know this shop_domain")
        if status == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                raise RateLimited(retry_after)  # defer the whole run instead of sleeping
            return _Retry(RateLimited(retry_after), retry_after)
        if status in _RETRYABLE_5XX:
            return _Retry(Unavailable(f"Judge.me HTTP {status}"), None)
        raise ClientRequestError(f"Judge.me HTTP {status}")

    async def fetch_page(self, page: int) -> Page:
        """GET one page. Raises a JudgeMeError subclass; never returns a partial page."""
        headers, params = self._request_parts(page)
        last: JudgeMeError = Unavailable("no attempt made")
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            transport=self._transport,
            follow_redirects=False,  # a redirect off api.judge.me must not carry the token
        ) as http:
            for attempt in range(1, self._max_attempts + 1):
                delay: float | None = None
                self.requests_made += 1
                try:
                    resp = await http.get(
                        f"{self._base_url}/reviews", headers=headers, params=params
                    )
                except httpx.TimeoutException:
                    last = Unavailable("Judge.me request timed out")
                except httpx.TransportError as exc:
                    last = Unavailable(f"Judge.me transport error: {type(exc).__name__}")
                else:
                    outcome = self._classify(resp)
                    if isinstance(outcome, Page):
                        return outcome
                    last, delay = outcome.error, outcome.delay
                if attempt < self._max_attempts:
                    self.retries += 1
                    await self._sleep(delay if delay is not None else self._backoff(attempt))
        raise last
