"""LEDGERED gpt-oss-120b spend on the shared Groq failover org, per hour over a trailing window, and the headroom it implies.

This is a LOWER bound on usage and so an UPPER bound on headroom: spend that wrote no ledger (other projects' keys in the same
org) is invisible, and Groq exposes no daily-remaining header (`x-ratelimit-remaining-tokens` is per minute). A zero count of
hours below one extraction therefore does not show the failover had room (S32 correction, docs/STATUS.md).

Groq's daily token pool is a rolling 24 h window per model and organisation. The failover key (`groq-api-key`)
lives in an organisation that other projects (triage-iq's cassette recorder, benchmarks) also spend from, so the
failover can only serve an extraction when the rolling 24 h spend leaves room for one.

Inputs are token ledgers (JSON with an `entries` list of `{ts, model, tokens}`; `ts` is ISO 8601 or epoch seconds)
plus the nightly failover probe, which makes one real failover extraction at 02:30 UTC. Only consumers that wrote a
ledger are counted: anything that spent from the org without one is invisible here, which is why the report also
carries the production 429/failover count and the probe history, checked separately in Cloud Logging.

Usage:
    python scripts/failover_headroom.py --end 2026-10-29T10:00:00+00:00 \
        --ledger shared=~/.groq_budget/ledger.json --ledger s31-reply-eval=D:/ml-cache/s31/reply/ledger.json \
        --out reports/ops/failover_headroom_s32.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

UTC = dt.UTC
MODEL_SUFFIX = "gpt-oss-120b"  # the failover model (ADR 0038)
DAILY_CAP = 200_000  # Groq free-tier pool per model, rolling 24 h (429 body, 2026-10-12)
PRODUCTION_RESERVE = 40_000  # never allocated by the shared ledger (triage-iq tools/groq_budget)
# One failover extraction as measured by the 2026-10-28 probe: tokens_in=1678, tokens_out=491.
EXTRACTION_TOKENS = 1_678 + 491
PROBE_HOUR_UTC = (2, 30)  # Scheduler `failover-probe-nightly`


def _ts(raw: str | float) -> dt.datetime:
    if isinstance(raw, int | float):
        return dt.datetime.fromtimestamp(raw, UTC)
    return dt.datetime.fromisoformat(raw).astimezone(UTC)


def load_events(ledgers: list[tuple[str, Path]]) -> list[tuple[dt.datetime, int, str]]:
    events = []
    for label, path in ledgers:
        for e in json.loads(path.read_text(encoding="utf-8"))["entries"]:
            if str(e["model"]).endswith(MODEL_SUFFIX):
                source = e.get("project", label) if label == "shared" else label
                events.append((_ts(e["ts"]), int(e["tokens"]), str(source)))
    return events


def probe_events(start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, int, str]]:
    # The day before `start` too: its probe still counts in the first hours' rolling window.
    day = start.date() - dt.timedelta(days=1)
    out = []
    while day <= end.date():
        t = dt.datetime(day.year, day.month, day.day, *PROBE_HOUR_UTC, tzinfo=UTC)
        out.append((t, EXTRACTION_TOKENS, "failover-probe"))
        day += dt.timedelta(days=1)
    return out


def headroom_report(
    events: list[tuple[dt.datetime, int, str]], end: dt.datetime, days: int
) -> dict:
    start = end - dt.timedelta(days=days)
    hours = [start + dt.timedelta(hours=h) for h in range(1, days * 24 + 1)]
    window = dt.timedelta(hours=24)
    used = [sum(n for t, n, _ in events if h - window < t <= h) for h in hours]
    head = [DAILY_CAP - u for u in used]
    worst = min(range(len(head)), key=head.__getitem__)
    by_source: dict[str, int] = {}
    for t, n, s in events:
        if start < t <= end:
            by_source[s] = by_source.get(s, 0) + n
    return {
        "window_utc": [start.isoformat(), end.isoformat()],
        "hours": len(hours),
        "scope": "ledgered spend only: lower bound on usage, upper bound on headroom; not a headroom measurement",
        "daily_cap": DAILY_CAP,
        "extraction_tokens": EXTRACTION_TOKENS,
        "tokens_in_window_by_source": dict(sorted(by_source.items())),
        "hours_ledgered_headroom_below_one_extraction": sum(h < EXTRACTION_TOKENS for h in head),
        "hours_ledgered_headroom_below_production_reserve": sum(
            h < PRODUCTION_RESERVE for h in head
        ),
        "max_possible_headroom_at_worst_hour_tokens": head[worst],
        "worst_hour_utc": hours[worst].isoformat(),
        "max_possible_extractions_at_worst_hour": head[worst] // EXTRACTION_TOKENS,
        "hourly": [
            {"hour_utc": h.isoformat(), "used_24h": u} for h, u in zip(hours, used, strict=True)
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--end", required=True, help="window end, ISO 8601 with offset")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument(
        "--ledger", action="append", required=True, help="label=path; label 'shared' keeps projects"
    )
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    end = _ts(a.end)
    ledgers = [(lbl, Path(p).expanduser()) for lbl, p in (x.split("=", 1) for x in a.ledger)]
    events = load_events(ledgers) + probe_events(end - dt.timedelta(days=a.days), end)
    report = headroom_report(events, end, a.days)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k != "hourly"}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
