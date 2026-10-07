"""Pre-batch Groq headroom guard: live signal + conservative local floor, FAILS CLOSED.

Why this exists (S19 Q3a): the earlier headroom check only summed what *this* script and prod
`extraction_costs` knew about. Eval runs, reply drafting and any other consumer of the same Groq key
never appear in `extraction_costs`, so the large pool (openai/gpt-oss-120b) was driven past 93% of
its 100K/day self-imposed ceiling while the check read "plenty of room".

What Groq actually exposes (verified by a live probe 2026-10-08 and Groq's rate-limit docs):
  * `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens` are PER-MINUTE (TPM, 8000 on the
    free tier) -- they say nothing about the daily token budget.
  * `x-ratelimit-limit-requests` / `x-ratelimit-remaining-requests` are the DAILY request budget
    (RPD, 1000) and are shared across every consumer of the key, so they ARE a live signal of
    other consumers' activity -- but in requests, not tokens.
  * The daily token budget (TPD) is exposed on NO successful response. It appears only in the body
    of a 429 once exceeded. There is no way to read "tokens used today" from Groq directly.

So the live signal is converted to a pessimistic token estimate (requests used x
PESSIMISTIC_TOKENS_PER_REQUEST) and combined with every local ledger. The batch is refused when
`est_cost > min(ceiling - local_floor, ceiling - live_estimate)`, and whenever the live signal is
missing or unparseable (fail closed: "could not verify" is a refusal, never a pass).

Over-refusal is the deliberate direction of error: a refused batch costs a rerun tomorrow; an
over-spend burns the shared quota that production draws on.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

DAILY_TOKEN_CEILING = 95_000  # 5% inside the 100K/model/UTC-day self-imposed budget (ADR 0031)
# Mean tokens per call for this repo's extraction/reply calls is ~2.1K-2.9K (ADR 0031 measured
# 2123 in + 790 out); 1_000 is a deliberately LOW-side-of-typical figure so that the request-count
# proxy does not refuse on a day of many tiny calls, while 500 requests already implies 500K if
# they were typical -- the proxy only needs to catch gross hidden usage, the local floor catches
# the rest.
PESSIMISTIC_TOKENS_PER_REQUEST = 1_000
LEDGER_WINDOW_SECONDS = 24 * 3600
_REQUIRED_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
)


class HeadroomRefusedError(RuntimeError):
    """The batch must not run. `.report` carries the per-model numbers."""

    def __init__(self, message: str, report: HeadroomReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class LiveSignal:
    limit_requests: int
    remaining_requests: int
    limit_tokens_per_minute: int
    remaining_tokens_per_minute: int

    @property
    def requests_used_today(self) -> int:
        return max(0, self.limit_requests - self.remaining_requests)


def parse_live_signal(headers: Mapping[str, str] | None) -> LiveSignal | None:
    """Parse Groq rate-limit headers. Returns None (never a default) if anything is missing/bad."""
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    try:
        vals = [int(str(lowered[h]).strip()) for h in _REQUIRED_HEADERS]
    except (KeyError, ValueError):
        return None
    lim_req, rem_req, lim_tok, rem_tok = vals
    if lim_req <= 0 or lim_tok <= 0 or rem_req < 0 or rem_tok < 0 or rem_req > lim_req:
        return None
    return LiveSignal(lim_req, rem_req, lim_tok, rem_tok)


@dataclass
class ModelHeadroom:
    model: str
    est_cost: int
    local_used: int
    live: LiveSignal | None
    ceiling: int
    ok: bool = False
    reason: str = ""
    ceiling_headroom: int = 0
    live_headroom: int | None = None

    @property
    def effective_headroom(self) -> int:
        if self.live_headroom is None:
            return 0
        return min(self.ceiling_headroom, self.live_headroom)


@dataclass
class HeadroomReport:
    models: list[ModelHeadroom] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.models) and all(m.ok for m in self.models)

    def summary(self) -> str:
        lines = []
        for m in self.models:
            live = "none" if m.live is None else f"{m.live.requests_used_today} req used today"
            lines.append(
                f"  {m.model}: est_cost={m.est_cost} local_used={m.local_used} live={live} "
                f"headroom(ceiling)={m.ceiling_headroom} headroom(live)={m.live_headroom} "
                f"-> {'OK' if m.ok else 'REFUSE: ' + m.reason}"
            )
        return "\n".join(lines)


def evaluate_model(
    model: str,
    est_cost: int,
    local_used: int,
    live: LiveSignal | None,
    *,
    ceiling: int = DAILY_TOKEN_CEILING,
    tokens_per_request: int = PESSIMISTIC_TOKENS_PER_REQUEST,
) -> ModelHeadroom:
    """Pure decision for one model. Refuses unless BOTH headrooms cover `est_cost`."""
    r = ModelHeadroom(model, est_cost, local_used, live, ceiling)
    r.ceiling_headroom = ceiling - local_used
    if live is None:
        r.reason = "live rate-limit headers missing or unparseable (fail closed)"
        return r
    live_estimate = live.requests_used_today * tokens_per_request
    r.live_headroom = ceiling - live_estimate
    if live.remaining_requests <= 0:
        r.reason = "daily request budget exhausted"
        return r
    if est_cost > r.effective_headroom:
        which = "ledger floor" if r.ceiling_headroom <= (r.live_headroom or 0) else "live signal"
        r.reason = f"est_cost {est_cost} > headroom {r.effective_headroom} ({which})"
        return r
    r.ok = True
    return r


def evaluate(
    est_cost_by_model: Mapping[str, int],
    local_used_by_model: Mapping[str, int],
    live_by_model: Mapping[str, LiveSignal | None],
    *,
    ceiling: int = DAILY_TOKEN_CEILING,
    tokens_per_request: int = PESSIMISTIC_TOKENS_PER_REQUEST,
) -> HeadroomReport:
    report = HeadroomReport()
    for model, est in est_cost_by_model.items():
        report.models.append(
            evaluate_model(
                model,
                est,
                local_used_by_model.get(model, 0),
                live_by_model.get(model),
                ceiling=ceiling,
                tokens_per_request=tokens_per_request,
            )
        )
    return report


def ledger_used(paths: Iterable[Path], now: float | None = None) -> dict[str, int]:
    """Sum trailing-24h tokens per model over DayLedger-format files ({"entries": [...]}).

    A ledger file that exists but cannot be parsed raises: an unreadable ledger must not be
    silently treated as empty (fail closed).
    """
    now = time.time() if now is None else now
    cutoff = now - LEDGER_WINDOW_SECONDS
    out: dict[str, int] = {}
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            continue
        for e in json.loads(text)["entries"]:
            if e["ts"] > cutoff:
                out[e["model"]] = out.get(e["model"], 0) + int(e["tokens"])
    return out


def merge_usage(*sources: Mapping[str, int]) -> dict[str, int]:
    """Add per-model usage from every local source (ledgers, prod extraction_costs, this run)."""
    out: dict[str, int] = {}
    for s in sources:
        for model, tok in s.items():
            out[model] = out.get(model, 0) + int(tok)
    return out


def probe_live_signal(model: str, api_key: str, *, timeout: float = 30.0) -> LiveSignal | None:
    """One minimal live call (~80 tokens) whose only purpose is reading the rate-limit headers.

    Any transport error or non-200 returns None, which `evaluate` turns into a refusal.
    """
    import httpx

    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 8,
                "reasoning_effort": "low",
            },
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    return parse_live_signal(dict(r.headers))


def preflight(
    est_cost_by_model: Mapping[str, int],
    *,
    local_used_by_model: Mapping[str, int] | None = None,
    live_by_model: Mapping[str, LiveSignal | None] | None = None,
    api_key: str | None = None,
    ceiling: int = DAILY_TOKEN_CEILING,
) -> HeadroomReport:
    """Run before every batch. Raises HeadroomRefusedError unless every model has headroom.

    `live_by_model` is injectable for tests; otherwise each model is probed with `api_key`
    (loaded from app settings by the caller -- never printed here).
    """
    live: dict[str, LiveSignal | None] = dict(live_by_model or {})
    if live_by_model is None:
        for model in est_cost_by_model:
            live[model] = probe_live_signal(model, api_key) if api_key else None
    report = evaluate(est_cost_by_model, local_used_by_model or {}, live, ceiling=ceiling)
    if not report.ok:
        raise HeadroomRefusedError(
            "Groq headroom check REFUSED the batch:\n" + report.summary(), report
        )
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refuse-or-pass Groq headroom check for a batch.")
    ap.add_argument("--est", action="append", required=True, metavar="MODEL=TOKENS")
    ap.add_argument(
        "--prod-used",
        action="append",
        default=[],
        metavar="MODEL=TOKENS",
        help="same-UTC-day prod usage from extraction_costs (read-only query)",
    )
    ap.add_argument("--ledger", action="append", default=[], type=Path)
    a = ap.parse_args(argv)

    def kv(items: list[str]) -> dict[str, int]:
        return {k: int(v) for k, v in (i.rsplit("=", 1) for i in items)}

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.config import get_settings

    used = merge_usage(kv(a.prod_used), ledger_used(a.ledger))
    try:
        report = preflight(kv(a.est), local_used_by_model=used, api_key=get_settings().groq_api_key)
    except HeadroomRefusedError as exc:
        print(exc)
        return 1
    print("Headroom OK:\n" + report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
