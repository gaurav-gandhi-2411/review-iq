"""Pre-batch Groq headroom guard: best-effort proxy + persisted 429 floor, FAILS CLOSED.

This guard does NOT read daily token headroom. Nothing on a successful Groq response exposes it.
What it does, honestly:
  * `x-ratelimit-*-tokens` are PER-MINUTE (TPM) and say nothing about the daily token budget.
  * `x-ratelimit-*-requests` are the daily REQUEST budget (RPD, resets at UTC midnight). They are
    a best-effort proxy: they see other consumers' request counts, but only for the current UTC
    day, and not their token size. Groq's TPD window appears to be ROLLING, so the proxy is blind
    to token draw from the previous day. Incident 2026-10-08: the proxy said OK (1 request used)
    and the first real call got 429 "TPD: Limit 200000, Used 199409, Requested 1678".
  * The only authoritative signal is that 429 body ("Limit N, Used M, Requested R"). It is parsed
    by `parse_tpd_429` and persisted per model (`record_tpd_observation`); `preflight` then
    refuses that model for 24h while the observation says it was past the ceiling fraction.
  * A passing preflight therefore means "no known reason to refuse", not "headroom confirmed":
    the first real call of the batch is the canary, and callers must stop on the first TPD 429.

The proxy is converted to a pessimistic token estimate (requests used x
PESSIMISTIC_TOKENS_PER_REQUEST) and combined with every local ledger. The batch is refused when
`est_cost > min(ceiling - local_floor, ceiling - live_estimate)`, when a fresh 429 observation
puts the model over the ceiling fraction, and whenever a signal is missing, unparseable or
unreadable (fail closed).

Over-refusal is the deliberate direction of error: a refused batch costs a rerun tomorrow; an
over-spend burns the shared quota that production draws on.
"""

from __future__ import annotations

import json
import re
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
# The ceiling is a fraction of the nominal 100K/day budget; a 429 reports the model's REAL limit
# (200000 for gpt-oss-120b on 2026-10-08), so the observation check compares used/limit to this.
CEILING_FRACTION = DAILY_TOKEN_CEILING / 100_000
OBSERVATION_TTL_SECONDS = 24 * 3600
# Local state, gitignored in the root .gitignore; never committed.
OBSERVATION_PATH = Path(__file__).resolve().parent / "results" / ".groq_tpd_observations.json"
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


_TPD_RE = re.compile(
    r"tokens per day \(TPD\)\D*?Limit\s+(\d+),\s*Used\s+(\d+),\s*Requested\s+(\d+)", re.I
)
_RETRY_RE = re.compile(r"try again in\s+((?:\d+(?:\.\d+)?[hms](?![a-z])\s*)+)", re.I)


def parse_tpd_429(text: str) -> tuple[int, int, int] | None:
    """(limit, used, requested) from a Groq TPD 429 body, else None (never a default)."""
    m = _TPD_RE.search(text or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_retry_after(text: str) -> float | None:
    """Seconds from 'Please try again in 2m12.19s' (h/m/s parts), else None. A hint only."""
    m = _RETRY_RE.search(text or "")
    if not m:
        return None
    parts = re.findall(r"(\d+(?:\.\d+)?)([hms])(?![a-z])", m[1].lower())
    return sum(float(v) * {"h": 3600, "m": 60, "s": 1}[u] for v, u in parts) if parts else None


@dataclass(frozen=True)
class TpdObservation:
    model: str
    ts: float
    limit: int
    used: int
    requested: int
    retry_after_s: float | None = None

    def refusal(self, est_cost: int, now: float, fraction: float = CEILING_FRACTION) -> str | None:
        """Reason to refuse, or None. Observations older than 24h no longer count."""
        if now - self.ts > OBSERVATION_TTL_SECONDS:
            return None
        if self.limit <= 0 or (self.used + est_cost) / self.limit > fraction:
            return (
                f"TPD 429 observed {(now - self.ts) / 3600:.1f}h ago: used {self.used}/"
                f"{self.limit} (+ est {est_cost}) exceeds {fraction:.0%} (rolling; fail closed)"
            )
        return None


def _read_observations(path: Path) -> dict[str, TpdObservation]:
    """Missing file = no observations. An unreadable file RAISES (fail closed, not 'none seen')."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    return {m: TpdObservation(**v) for m, v in json.loads(text).items()}


def record_tpd_observation(
    model: str, text: str, *, now: float | None = None, path: Path = OBSERVATION_PATH
) -> TpdObservation | None:
    """If `text` is a TPD 429 body, persist it as the model's latest observation (newest wins)."""
    parsed = parse_tpd_429(text)
    if parsed is None:
        return None
    obs = TpdObservation(
        model, time.time() if now is None else now, *parsed, retry_after_s=parse_retry_after(text)
    )
    try:
        current = _read_observations(path)
    except (ValueError, TypeError, KeyError):
        current = {}  # corrupt file: replaced by this fresher, authoritative observation
    current[model] = obs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({k: vars(v) for k, v in current.items()}, indent=2) + "\n", encoding="utf-8"
    )
    return obs


def check_persisted_floor(
    est_cost_by_model: Mapping[str, int], *, now: float | None = None, path: Path = OBSERVATION_PATH
) -> dict[str, str]:
    """{model: refusal reason} from persisted 429 observations. No network. Unreadable => refuse."""
    now = time.time() if now is None else now
    try:
        obs = _read_observations(path)
    except (ValueError, TypeError, KeyError, OSError):
        return {m: "TPD observation file unreadable (fail closed)" for m in est_cost_by_model}
    out = {}
    for model, est in est_cost_by_model.items():
        reason = obs[model].refusal(est, now) if model in obs else None
        if reason:
            out[model] = reason
    return out


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
    floor_reason: str | None = None,
) -> ModelHeadroom:
    """Pure decision for one model. Refuses unless BOTH headrooms cover `est_cost`."""
    r = ModelHeadroom(model, est_cost, local_used, live, ceiling)
    r.ceiling_headroom = ceiling - local_used
    if floor_reason:
        r.reason = floor_reason
        return r
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
    floors: Mapping[str, str] | None = None,
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
                floor_reason=(floors or {}).get(model),
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
    observation_path: Path = OBSERVATION_PATH,
) -> HeadroomReport:
    """Run before every batch. Raises HeadroomRefusedError unless every model has headroom.

    `live_by_model` is injectable for tests; otherwise each model is probed with `api_key`
    (loaded from app settings by the caller -- never printed here).
    """
    floors = check_persisted_floor(est_cost_by_model, path=observation_path)
    live: dict[str, LiveSignal | None] = dict(live_by_model or {})
    if live_by_model is None:
        for model in est_cost_by_model:
            if model in floors:
                continue  # already refused by a 429 observation; spend no probe call
            live[model] = probe_live_signal(model, api_key) if api_key else None
    report = evaluate(
        est_cost_by_model, local_used_by_model or {}, live, ceiling=ceiling, floors=floors
    )
    if not report.ok:
        raise HeadroomRefusedError(
            "Groq headroom check REFUSED the batch:\n" + report.summary(), report
        )
    return report
