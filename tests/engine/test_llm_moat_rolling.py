from __future__ import annotations

from engine.intent.llm_moat import WINDOW_S, wait_seconds


def test_fits_under_cap_no_wait():
    assert wait_seconds([(0.0, 40_000)], 1_000, now=100.0, cap=100_000) == 0.0


def test_waits_until_oldest_entry_ages_out():
    entries = [(0.0, 60_000), (3_600.0, 40_000)]
    # 100K used; the first entry (60K) expires at t=WINDOW_S, which is the earliest point 1K fits.
    assert wait_seconds(entries, 1_000, now=7_200.0, cap=100_000) == WINDOW_S - 7_200.0


def test_expired_entries_do_not_count():
    entries = [(0.0, 90_000)]
    assert wait_seconds(entries, 5_000, now=WINDOW_S + 1.0, cap=100_000) == 0.0


def test_request_larger_than_cap_waits_full_window():
    assert wait_seconds([], 200_000, now=0.0, cap=100_000) == float(WINDOW_S)
