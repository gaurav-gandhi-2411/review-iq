"""Trailing-24h token ledger for the keyless demo endpoint.

Why this exists: Groq's per-model daily token limit (200,000 on the production org) is a ROLLING
24h window, not a midnight reset (verified from a 429 body: ``tokens per day (TPD): Limit 200000,
Used 199445, Requested 972. Please try again in 3m0.144s``). The demo's own per-UTC-date request
counter (storage_pg.check_and_increment_demo_request_pg) therefore says nothing about how much of
that window the demo has actually used. This ledger counts the demo's tokens over the trailing 24
hours so the demo can be held to a fixed share of the pool and production keeps its reserve.

Design (smallest correct thing, no DB migration):
- In-memory and per process. Cloud Run runs up to ``demo_max_instances`` instances, so each one
  enforces ``pool * share / instances``; the fleet total can then never exceed ``pool * share``.
  A cold start empties the ledger, so a restart can briefly re-admit; the DB-backed daily request
  budget stays in place as the cross-instance backstop. Persisting the ledger would need a new
  table or a cross-tenant read policy on extraction_costs (a migration, which needs an ADR and
  is out of scope here).
- A recent TPD 429 observed by the router is held for its retry-after TTL (a minimal copy of the
  eval/quota_guard idea; eval code is not importable from app/).
- The clock is injectable so tests need no sleeping.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS: float = 24 * 3600.0
# A TPD 429 whose retry hint cannot be parsed is held this long, then re-probed by the next call.
DEFAULT_429_HOLD_SECONDS: float = 300.0

_RETRY_RE = re.compile(r"try again in\s+((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", re.IGNORECASE)
_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def retry_hint_seconds(message: str) -> float | None:
    """Seconds from a Groq 'try again in 3m0.144s' hint, or None when absent."""
    m = _RETRY_RE.search(message)
    if not m:
        return None
    return sum(float(n) * _UNIT[u] for n, u in _PART_RE.findall(m.group(1)))


def is_tpd_message(message: str) -> bool:
    lowered = message.lower()
    return "tokens per day" in lowered or "(tpd)" in lowered


def demo_ceiling_tokens(pool_tokens: int, share: float, instances: int) -> int:
    """Per-process demo ceiling. ``share`` is clamped to [0, 0.5] so >=50% stays with production."""
    bounded = min(max(share, 0.0), 0.5)
    return int(pool_tokens * bounded // max(instances, 1))


class TrailingTokenLedger:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        # [timestamp, tokens]; a list so settle() can replace an estimate with the real count.
        self._entries: deque[list[float]] = deque()
        self._blocked_until: float = 0.0

    def _prune(self, now: float) -> None:
        while self._entries and self._entries[0][0] <= now - WINDOW_SECONDS:
            self._entries.popleft()

    def used(self) -> int:
        now = self._clock()
        self._prune(now)
        return int(sum(e[1] for e in self._entries))

    def blocked_for(self) -> float:
        """Seconds a recently observed TPD 429 still blocks admission (0 when clear)."""
        return max(0.0, self._blocked_until - self._clock())

    def retry_after_seconds(self, estimate: int, ceiling: int) -> float:
        """Seconds until ``estimate`` more tokens fit under ``ceiling`` (0 if they fit now)."""
        now = self._clock()
        self._prune(now)
        wait = self.blocked_for()
        total = sum(e[1] for e in self._entries)
        for ts, tokens in self._entries:
            if total + estimate <= ceiling:
                break
            total -= tokens
            wait = max(wait, ts + WINDOW_SECONDS - now)
        return wait if total + estimate <= ceiling else max(wait, WINDOW_SECONDS)

    def try_admit(self, estimate: int, ceiling: int) -> list[float] | None:
        """Reserve ``estimate`` tokens if usage + estimate fits the ceiling, else return None.

        Check and append have no await between them, so concurrent asyncio callers cannot both
        pass on the same headroom. The returned entry is handed to :meth:`settle`.
        """
        if self.blocked_for() > 0:
            return None
        if self.used() + estimate > ceiling:
            return None
        entry = [self._clock(), float(estimate)]
        self._entries.append(entry)
        return entry

    @staticmethod
    def settle(entry: list[float], actual_tokens: int) -> None:
        """Replace the admission-time estimate with the real token count."""
        entry[1] = float(actual_tokens)

    def observe_rate_limit(self, hold_seconds: float | None) -> None:
        """Remember a TPD 429 for ``hold_seconds`` (capped at the window)."""
        hold = DEFAULT_429_HOLD_SECONDS if hold_seconds is None else hold_seconds
        self._blocked_until = max(self._blocked_until, self._clock() + min(hold, WINDOW_SECONDS))

    def clear(self) -> None:
        """Reset all state. Exposed for test isolation only."""
        self._entries.clear()
        self._blocked_until = 0.0


# Process-wide ledger shared by the demo endpoint and the router's 429 observation.
demo_ledger = TrailingTokenLedger()


def observe_groq_429(message: str) -> None:
    """Router hook: hold the demo off after a TPD 429 on any call in this process."""
    if is_tpd_message(message):
        demo_ledger.observe_rate_limit(retry_hint_seconds(message))
