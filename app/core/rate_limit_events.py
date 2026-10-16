"""Structured visibility for provider 429s (Groq and the OpenRouter secondary).

Before this module a Groq 429 inside ``groq_tiered`` surfaced only as a body-less
``router.api_error`` warning, and a silent failover to the secondary was invisible. Every 429 now
emits one ``llm.rate_limited`` WARNING event (so it shows up in Cloud Run ``severity>=WARNING``
queries) plus a Prometheus counter.

Only structural facts are logged: provider, model, tier, org id string, limit type, the
Limit/Used/Requested numbers, the retry hint and numeric ``x-ratelimit-*`` header values. Never
keys, prompts or review text. Parsing never raises: a body we cannot read yields an event with
the fields it could recover and ``None`` for the rest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import structlog

from app.core.metrics import RATE_LIMITED_TOTAL

log = structlog.get_logger(__name__)

_LIMIT_TYPE_RE = re.compile(r"\((TPM|RPM|TPD|RPD)\)")
# Fallback for bodies that spell the window out without the abbreviation.
_LIMIT_TYPE_WORDS: tuple[tuple[str, str], ...] = (
    ("tokens per minute", "TPM"),
    ("requests per minute", "RPM"),
    ("tokens per day", "TPD"),
    ("requests per day", "RPD"),
)
_NUMBERS_RE = re.compile(
    r"Limit\s+(?P<limit>\d+),\s*Used\s+(?P<used>\d+),\s*Requested\s+(?P<requested>\d+)",
    re.IGNORECASE,
)
_ORG_RE = re.compile(r"organization\s+`?(?P<org>org_[A-Za-z0-9]+)`?")
_RETRY_RE = re.compile(r"try again in\s+(?P<dur>(?:\d+(?:\.\d+)?(?:h|ms|m|s))+)", re.IGNORECASE)
_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def _message_from_body(body: Any, fallback: str) -> str:
    """Best-effort human message from a dict / JSON-string / None body."""
    if isinstance(body, (str, bytes)):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    if isinstance(body, Mapping):
        err = body.get("error", body)
        if isinstance(err, Mapping):
            msg = err.get("message")
            if isinstance(msg, str):
                return msg
        elif isinstance(err, str):
            return err
    return fallback


def parse_retry_seconds(message: str) -> float | None:
    """Parse 'try again in 3m0.144s' / '1h2m3s' / '250ms' into seconds, else None."""
    m = _RETRY_RE.search(message)
    if not m:
        return None
    return round(
        sum(float(n) * _UNIT_SECONDS[u] for n, u in _DURATION_PART_RE.findall(m.group("dur"))), 3
    )


def parse_rate_limit_message(message: str) -> dict[str, Any]:
    """Extract limit_type, org_id, limit/used/requested and retry_after_s from a 429 message."""
    out: dict[str, Any] = {
        "limit_type": None,
        "org_id": None,
        "limit": None,
        "used": None,
        "requested": None,
        "retry_after_s": parse_retry_seconds(message),
    }
    lt = _LIMIT_TYPE_RE.search(message)
    if lt:
        out["limit_type"] = lt.group(1)
    else:
        lowered = message.lower()
        for phrase, abbr in _LIMIT_TYPE_WORDS:
            if phrase in lowered:
                out["limit_type"] = abbr
                break
    org = _ORG_RE.search(message)
    if org:
        out["org_id"] = org.group("org")
    nums = _NUMBERS_RE.search(message)
    if nums:
        out["limit"] = int(nums.group("limit"))
        out["used"] = int(nums.group("used"))
        out["requested"] = int(nums.group("requested"))
    return out


def numeric_ratelimit_headers(headers: Any) -> dict[str, float]:
    """Numeric ``x-ratelimit-*`` / ``retry-after`` header values; non-numeric ones are dropped."""
    out: dict[str, float] = {}
    try:
        items = list(headers.items())
    except Exception:  # noqa: BLE001  # headers may be absent/mocked; never let logging raise
        return out
    for key, value in items:
        name = str(key).lower()
        if not (name.startswith("x-ratelimit-") or name == "retry-after"):
            continue
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def log_rate_limited(
    *,
    provider: str,
    model: str,
    tier: str | None,
    role: str,
    body: Any,
    headers: Any,
    fallback_message: str = "",
) -> dict[str, Any]:
    """Emit one ``llm.rate_limited`` WARNING and bump the counter. Returns the logged fields.

    ``role`` is ``primary`` or ``secondary`` (the failover decision itself is logged by
    ``llm.failover`` in app/core/llm.py, which knows whether a secondary was taken).
    """
    message = _message_from_body(body, fallback_message)
    fields = parse_rate_limit_message(message)
    hdrs = numeric_ratelimit_headers(headers)
    if fields["retry_after_s"] is None and "retry-after" in hdrs:
        fields["retry_after_s"] = hdrs["retry-after"]
    log.warning(
        "llm.rate_limited",
        provider=provider,
        model=model,
        tier=tier,
        role=role,
        status_code=429,
        ratelimit_headers=hdrs,
        **fields,
    )
    RATE_LIMITED_TOTAL.labels(
        provider=provider,
        model=model,
        tier=tier or "none",
        limit_type=fields["limit_type"] or "unknown",
    ).inc()
    return fields
