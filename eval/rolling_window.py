"""Trailing-24h token accounting for Groq's per-model TPD limit (eval/benchmark traffic).

VERIFIED 2026-10-12 00:07 UTC from a Groq 429 body on Samidha's production org:
    "Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 199445, Requested 972.
     Please try again in 3m0s"
That is a ROLLING 24-hour window (capacity returns continuously, minutes later), not a reset at
00:00 UTC. Nothing here may assume a calendar-day boundary.

Policy (GG, 2026-10-12): ALL eval/benchmark traffic on the production org is hard-capped at
EVAL_CAP_TOKENS_PER_MODEL tokens per model per rolling 24h; production keeps the other 50%.

Allowed spend = cap - sum(tokens with ts > now - 24h). Pure functions over `{"ts", "model",
"tokens"}` entries with an injectable `now`, so every caller is testable with no clock and no
network. Entries that only know a UTC date (legacy ledgers) are placed at the END of that day:
the latest possible spend time, so they stay in the window longest (over-refusal, never
over-spend).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

WINDOW_SECONDS = 24 * 3600
# Half of the 200K per-model TPD pool (see the VERIFIED 429 above); the other half is production's.
EVAL_CAP_TOKENS_PER_MODEL = 100_000


def date_to_end_of_day_ts(utc_date: str) -> float:
    """Epoch seconds of the last second of `utc_date` (YYYY-MM-DD, UTC). Conservative placement
    for usage whose true spend time was not recorded."""
    day = datetime.strptime(utc_date, "%Y-%m-%d").replace(tzinfo=UTC)
    return (day + timedelta(days=1)).timestamp() - 1


def used_in_window(
    entries: Iterable[Mapping[str, Any]], model: str, now: float, window: int = WINDOW_SECONDS
) -> int:
    """Tokens charged to `model` with ts in (now - window, now]. Future-dated entries count too
    (clock skew must not under-count)."""
    cutoff = now - window
    return sum(int(e["tokens"]) for e in entries if e["model"] == model and e["ts"] > cutoff)


def remaining(
    entries: Iterable[Mapping[str, Any]],
    model: str,
    now: float,
    cap: int = EVAL_CAP_TOKENS_PER_MODEL,
    window: int = WINDOW_SECONDS,
) -> int:
    """Spend still allowed for `model` right now (never negative)."""
    return max(0, cap - used_in_window(entries, model, now, window))


def next_allowed_at(
    entries: Iterable[Mapping[str, Any]],
    model: str,
    now: float,
    need: int,
    cap: int = EVAL_CAP_TOKENS_PER_MODEL,
    window: int = WINDOW_SECONDS,
) -> float | None:
    """Earliest epoch time at which `need` tokens fit under `cap`, assuming no new spend.
    `now` if it already fits; None if `need` > cap (can never fit)."""
    if need > cap:
        return None
    live = sorted(
        (e for e in entries if e["model"] == model and e["ts"] > now - window),
        key=lambda e: e["ts"],
    )
    used = sum(int(e["tokens"]) for e in live)
    at = now
    for e in live:
        if used + need <= cap:
            break
        used -= int(e["tokens"])
        at = max(at, e["ts"] + window)
    return at
