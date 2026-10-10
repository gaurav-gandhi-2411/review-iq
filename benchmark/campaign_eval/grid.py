"""Threshold grid, vectorised alert rule, cooldown episodes and detection outcomes.

`detector_alerts` re-implements app.core.detectors.campaign.is_alert over a whole parameter grid
with numpy; tests/unit/test_campaign_eval_harness.py checks it point-for-point against the
production function so the evaluated rule IS the shipped rule.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
from app.core.detectors.campaign_signals import CampaignParams

from benchmark.campaign_eval.trace import SIMS, Trace

DAY_S = 86400.0
COOLDOWN_S = CampaignParams().cooldown_days * DAY_S
THETA_M = CampaignParams().theta_mismatch
MIN_ATTRIBUTED = 3  # campaign reviews that must sit inside the firing window (spec section 5)
DETECT_GRACE_S = DAY_S  # alert may open up to 24h after the last campaign review

B1_THETAS = np.arange(2.0, 40.0, 1.0)
B2_THETAS = np.round(np.arange(0.3, 3.0, 0.1), 2)


@dataclass(frozen=True)
class Grid:
    s_idx: np.ndarray
    k_min: np.ndarray
    theta_b: np.ndarray
    theta_r: np.ndarray
    strong: np.ndarray
    use_m: np.ndarray

    def __len__(self) -> int:
        return len(self.s_idx)

    def params(self, c: int) -> dict[str, float | int | bool]:
        return {
            "similarity": SIMS[int(self.s_idx[c])],
            "k_min": int(self.k_min[c]),
            "theta_burst": float(self.theta_b[c]),
            "theta_rating": float(self.theta_r[c]),
            "strong_multiplier": float(self.strong[c]),
            "use_mismatch": bool(self.use_m[c]),
        }


def build_grid() -> Grid:
    rows = list(
        itertools.product(
            range(len(SIMS)),
            (3, 4),
            (3.0, 4.0, 5.0, 6.0),
            (2.5, 3.0, 3.5, 4.0),
            (1.5, 2.0, 3.0),
            (0, 1),
        )
    )
    cols = list(zip(*rows, strict=True))
    return Grid(
        s_idx=np.array(cols[0]),
        k_min=np.array(cols[1]),
        theta_b=np.array(cols[2]),
        theta_r=np.array(cols[3]),
        strong=np.array(cols[4]),
        use_m=np.array(cols[5], dtype=bool),
    )  # 4*2*4*4*3*2 = 768 combinations


def detector_alerts(tr: Trace, g: Grid, sel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(any_alert (n,P) bool, campaign reviews in the narrowest firing window (n,P))."""
    th_b = g.theta_b[sel][:, None, None]
    th_r = g.theta_r[sel][:, None, None]
    c = g.strong[sel][:, None, None]
    kmin = g.k_min[sel][:, None, None]
    um = g.use_m[sel][:, None, None]
    burst, absrz, mz = tr.burst[None], tr.absrz[None], tr.mz[None]
    kk = np.moveaxis(tr.k[:, :, g.s_idx[sel]], 2, 0)
    fired = (
        (burst >= th_b).astype(np.int8) + (kk >= kmin) + (absrz >= th_r) + (um & (mz >= THETA_M))
    )
    strong = (
        (burst >= c * th_b)
        | (kk >= np.ceil(c * kmin))
        | (absrz >= c * th_r)
        | (um & (mz >= c * THETA_M))
    )
    cond = (fired >= 2) | strong
    first_w = cond.argmax(2)
    attr = tr.camp[np.arange(len(tr.t))[None, :], first_w]
    return cond.any(2), attr


def baseline_alerts(tr: Trace, kind: str, thetas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    # invalid points are encoded below every threshold in the grids (-1 and -99)
    stat, attr = (tr.b1z, tr.b1camp) if kind == "b1" else (tr.b2d, tr.b2camp)
    cond = stat[None, :] >= thetas[:, None]
    return cond, np.broadcast_to(attr, cond.shape)


def open_alerts(t: np.ndarray, alert_pts: np.ndarray) -> list[int]:
    """Episodes: a new alert is opened only COOLDOWN_S after the previous one opened."""
    opened: list[int] = []
    at = t[alert_pts]
    i = 0
    while i < len(alert_pts):
        opened.append(int(alert_pts[i]))
        i = max(int(np.searchsorted(at, at[i] + COOLDOWN_S, side="left")), i + 1)
    return opened


def clean_false_alerts(tr: Trace, any_alert: np.ndarray) -> np.ndarray:
    """Alert episodes per combination on a clean stream."""
    return np.array([len(open_alerts(tr.t, np.flatnonzero(row))) for row in any_alert])


def injected_outcomes(
    tr: Trace, any_alert: np.ndarray, attr: np.ndarray, camp_ts: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(detected bool, campaign reviews posted at/before the alert, hours from first campaign
    review) per combination. Detected = an opened alert inside [first, last + 24h] whose firing
    window holds >= MIN_ATTRIBUTED campaign reviews."""
    n = any_alert.shape[0]
    det = np.zeros(n, dtype=bool)
    ttd_n = np.full(n, -1, dtype=np.int32)
    ttd_h = np.full(n, np.nan)
    lo, hi = camp_ts[0], camp_ts[-1] + DETECT_GRACE_S
    for c in range(n):
        for p in open_alerts(tr.t, np.flatnonzero(any_alert[c])):
            if lo <= tr.t[p] <= hi and attr[c, p] >= MIN_ATTRIBUTED:
                det[c] = True
                ttd_n[c] = int(np.searchsorted(camp_ts, tr.t[p], side="right"))
                ttd_h[c] = (tr.t[p] - camp_ts[0]) / 3600.0
                break
    return det, ttd_n, ttd_h
