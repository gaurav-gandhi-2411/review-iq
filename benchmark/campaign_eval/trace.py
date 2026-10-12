"""Evidence traces: for a set of evaluation points on a ProductStream, the detector's per-window
signal values for every similarity in the tuning grid, plus the two baselines' statistics.

A trace is independent of every tuned threshold except the similarity (kept per grid value), so
thresholds are applied afterwards by grid.py without recomputing evidence. Invalid values are
encoded as -1 (burst, |rating z|, mismatch z) or 0 (template size); thresholds are all >= 2 so -1
can never fire.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from app.core.detectors.campaign_signals import DAY_S, HOUR_S, ProductStream

WINDOWS = (6, 24, 72, 168)
SIMS = (0.4, 0.5, 0.6, 0.7)
B1_WINDOW_IDX = WINDOWS.index(24)


@dataclass
class Trace:
    t: np.ndarray  # (P,) epoch seconds of each evaluation point
    burst: np.ndarray  # (P, W) -log10 tail prob, -1 invalid
    k: np.ndarray  # (P, W, S) largest near-identical group, 0 if none/invalid
    absrz: np.ndarray  # (P, W) |rating z|, -1 invalid
    mz: np.ndarray  # (P, W) mismatch z, -1 invalid
    camp: np.ndarray  # (P, W) campaign reviews inside each window
    b1z: np.ndarray  # (P,) baseline 1 z-score of the trailing-24h count, -1 invalid
    b1camp: np.ndarray  # (P,) campaign reviews in that 24h window
    b2d: np.ndarray  # (P,) baseline 2 rating drop (history mean - last-10 mean), -99 invalid
    b2camp: np.ndarray  # (P,) campaign reviews among the last 10


def trace_stream(
    stream: ProductStream, points: Sequence[int], camp_mask: np.ndarray | None = None
) -> Trace:
    n = len(stream)
    ts = stream.ts
    cc = np.zeros(n + 1, dtype=np.int64)
    if camp_mask is not None:
        cc[1:] = np.cumsum(camp_mask.astype(np.int64))
    P, W, S = len(points), len(WINDOWS), len(SIMS)
    t = np.empty(P)
    burst = np.full((P, W), -1.0)
    k = np.zeros((P, W, S), dtype=np.int16)
    absrz = np.full((P, W), -1.0)
    mz = np.full((P, W), -1.0)
    camp = np.zeros((P, W), dtype=np.int32)
    b1z = np.full(P, -1.0)
    b1camp = np.zeros(P, dtype=np.int32)
    b2d = np.full(P, -99.0)
    b2camp = np.zeros(P, dtype=np.int32)
    for p, i in enumerate(points):
        t[p] = ts[i]
        for w, hours in enumerate(WINDOWS):
            lo = bisect_right(ts, ts[i] - hours * HOUR_S)
            camp[p, w] = cc[i + 1] - cc[lo]
            ev = stream.evidence_at(i, hours)
            if ev is None:
                continue
            burst[p, w] = ev.burst if ev.burst is not None else -1.0
            if ev.rating_z is not None:
                absrz[p, w] = abs(ev.rating_z)
            if ev.mismatch_z is not None:
                mz[p, w] = ev.mismatch_z
            for s, sim in enumerate(SIMS):
                k[p, w, s] = stream.largest_cluster(lo, i, sim)[0]
            if w == B1_WINDOW_IDX:
                b1z[p] = _b1_z(stream, ev.n, ts[i] - 24 * HOUR_S)
                b1camp[p] = cc[i + 1] - cc[lo]
        if i >= 9:
            b2d[p], b2camp[p] = _b2_drop(stream, i, cc)
    return Trace(t, burst, k, absrz, mz, camp, b1z, b1camp, b2d, b2camp)


def _b1_z(stream: ProductStream, n_w: int, start: float) -> float:
    """Baseline 1: trailing-24h count vs the mean/sd of daily counts over the preceding 90 days
    (sd floored at 0.5). -1 when fewer than 14 days of daily history exist."""
    d_end = min(int(start // DAY_S) - stream.day0, len(stream.dc) - 1)
    d_beg = max(0, d_end - 90)
    m = d_end - d_beg
    if m < 14:
        return -1.0
    total = stream.dc[d_end] - stream.dc[d_beg]
    sq = stream.dq[d_end] - stream.dq[d_beg]
    mean = total / m
    sd = max(math.sqrt(max(0.0, (sq - total * total / m) / (m - 1))), 0.5)
    return (n_w - mean) / sd


def _b2_drop(stream: ProductStream, i: int, cc: np.ndarray) -> tuple[float, int]:
    """Baseline 2: mean rating of the preceding 90 days minus the mean of the last 10 reviews.
    -99 when the history holds fewer than 30 rated reviews or spans under 30 days."""
    ts = stream.ts
    j = i - 9
    camp = int(cc[i + 1] - cc[j])
    if stream.cr[i + 1] - stream.cr[j] != 10 or ts[j] - ts[0] < 30 * DAY_S:
        return -99.0, camp
    lo_h = bisect_left(ts, ts[j] - 90 * DAY_S)
    n_h = stream.cr[j] - stream.cr[lo_h]
    if n_h < 30:
        return -99.0, camp
    h_mean = (stream.sr[j] - stream.sr[lo_h]) / n_h
    m10 = (stream.sr[i + 1] - stream.sr[j]) / 10
    return h_mean - m10, camp
