"""Replay the api-health check_passed series against candidate failure-rate thresholds.

Read-only. Input is a JSON dump {checker_location: [[endTime, bool], ...]} of the raw
`uptime_check/check_passed` series (see ops/monitoring/README.md, "Threshold replay").
Monitoring samples check_passed every 10 s per region, so ONE failed check execution
shows up as ~6 consecutive false points; a run of false points is therefore one failed
check. We count failed checks inside a sliding window and report the distinct alert
episodes each (window, min_failed_checks) pair would have opened.
"""

from __future__ import annotations

import datetime as dt
import json
import sys

RUN_GAP_S = 15  # false points >15 s apart belong to different failed checks (sampling is 10 s)


def _ts(s: str) -> dt.datetime:
    base, _, frac = s.rstrip("Z").partition(".")
    return dt.datetime.fromisoformat(base + ("." + frac[:6] if frac else ""))


def failed_checks(series: dict[str, list[list]]) -> list[tuple[dt.datetime, str]]:
    """Collapse runs of consecutive false points per region into one failed check each."""
    out: list[tuple[dt.datetime, str]] = []
    for loc, pts in series.items():
        last: dt.datetime | None = None
        for t, ok in sorted((_ts(p[0]), p[1]) for p in pts):
            if ok:
                continue
            if last is None or (t - last).total_seconds() > RUN_GAP_S:
                out.append((t, loc))
            last = t
    return sorted(out)


def episodes(checks: list[tuple[dt.datetime, str]], window_min: int, threshold: int) -> list[str]:
    """Start time of each episode in which >= threshold failed checks sit inside the window."""
    win = dt.timedelta(minutes=window_min)
    fired: list[dt.datetime] = []
    breaching_until: dt.datetime | None = None
    for i, (t, _) in enumerate(checks):
        n = sum(1 for u, _ in checks[: i + 1] if t - win < u <= t)
        if n >= threshold:
            if breaching_until is None or t > breaching_until:
                fired.append(t)
            breaching_until = t + win
    return [t.strftime("%m-%d %H:%M") for t in fired]


def main(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        checks = failed_checks(json.load(fh))
    print(f"failed checks: {len(checks)}")
    for t, loc in checks:
        print("  ", t.strftime("%m-%d %H:%M:%S"), loc)
    for window_min, threshold in [(15, 2), (15, 3), (15, 4), (10, 3), (30, 3)]:
        eps = episodes(checks, window_min, threshold)
        print(f"window={window_min}m threshold>={threshold}: {len(eps)} episodes {eps}")


if __name__ == "__main__":
    main(sys.argv[1])
