from __future__ import annotations

from eval import rolling_window as rw

M = "openai/gpt-oss-20b"
NOW = 1_800_000_000.0
H = 3600


def _e(age_h: float, tokens: int, model: str = M) -> dict[str, object]:
    return {"ts": NOW - age_h * H, "model": model, "tokens": tokens}


def test_cap_is_the_100k_policy() -> None:
    assert rw.EVAL_CAP_TOKENS_PER_MODEL == 100_000


def test_window_is_trailing_24h_not_a_calendar_day() -> None:
    entries = [_e(23.9, 40_000), _e(24.1, 50_000), _e(1, 10_000), _e(1, 7, "other")]
    assert rw.used_in_window(entries, M, NOW) == 50_000  # 24.1h-old entry excluded
    assert rw.remaining(entries, M, NOW) == 50_000
    # a few minutes later the 23.9h entry has also rolled off: capacity returns continuously
    assert rw.used_in_window(entries, M, NOW + 0.2 * H) == 10_000


def test_entry_exactly_24h_old_is_out() -> None:
    assert rw.used_in_window([_e(24, 5)], M, NOW) == 0


def test_remaining_never_negative_and_future_entries_count() -> None:
    assert rw.remaining([_e(1, 150_000)], M, NOW) == 0
    assert rw.used_in_window([_e(-1, 9)], M, NOW) == 9


def test_next_allowed_at() -> None:
    entries = [_e(20, 60_000), _e(10, 35_000)]  # 95K used
    assert rw.next_allowed_at(entries, M, NOW, 5_000) == NOW  # fits now
    # need 30K: the 60K entry must leave the window, 4h from now
    assert rw.next_allowed_at(entries, M, NOW, 30_000) == NOW + 4 * H
    assert rw.next_allowed_at(entries, M, NOW, 100_001) is None
    assert rw.next_allowed_at([], M, NOW, 100_000) == NOW


def test_date_only_usage_is_placed_at_end_of_day() -> None:
    assert rw.date_to_end_of_day_ts("1970-01-02") == 2 * 86400 - 1


def test_llm_baseline_ledger_uses_ts_and_falls_back_to_end_of_utc_date() -> None:
    import pytest

    pytest.importorskip("sklearn")
    from engine.experiments import llm_baseline as lb

    recs = [
        {"ts": NOW - 1 * H, "utc_date": "x", "tokens_in": 100, "tokens_out": 20},
        {"utc_date": "1970-01-02", "tokens_in": 5, "tokens_out": 5},  # legacy, no ts
    ]
    led = lb._ledger(recs, M)
    assert [e["tokens"] for e in led] == [120, 10]
    assert led[1]["ts"] == 2 * 86400 - 1
    assert rw.used_in_window(led, M, NOW) == 120  # the 1970 record is long out of the window
    assert lb.ROLLING_CAP == rw.EVAL_CAP_TOKENS_PER_MODEL
