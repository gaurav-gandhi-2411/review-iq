"""Pin the failure-rate replay logic (ops/monitoring/replay_failure_rate.py)."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "ops" / "monitoring" / "replay_failure_rate.py"
_spec = importlib.util.spec_from_file_location("replay_failure_rate", _PATH)
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)


def _run(start: str, n: int = 6) -> list[list]:
    t0 = dt.datetime.fromisoformat(start)
    return [[(t0 + dt.timedelta(seconds=10 * i)).isoformat(), False] for i in range(n)]


def test_a_run_of_false_samples_is_one_failed_check_and_a_lone_blip_never_fires() -> None:
    checks = replay.failed_checks({"eur-belgium": _run("2026-10-10T10:00:00")})
    assert len(checks) == 1
    assert replay.episodes(checks, 15, 3) == []


def test_three_regions_failing_together_open_exactly_one_episode() -> None:
    burst = {
        "a": _run("2026-10-10T11:00:00"),
        "b": _run("2026-10-10T11:00:10"),
        "c": _run("2026-10-10T11:00:20"),
    }
    assert len(replay.episodes(replay.failed_checks(burst), 15, 3)) == 1
