"""Metrics, product-level bootstrap and pre-registered threshold selection (spec sections 5-8).

Bootstrap unit = PRODUCT (stream): a resample draws products with replacement and carries all of
a product's clean exposure and injections together (2000 resamples, seed 42, percentile 95% CI).
"""

from __future__ import annotations

import numpy as np

N_BOOT = 2000
SEED = 42
BUDGETS = (1.0, 0.1)
RHOS = {"1_per_12_product_months": 1 / 12, "1_per_36_product_months": 1 / 36}


def _weights(n_streams: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, n_streams, size=(N_BOOT, n_streams))
    w = np.zeros((N_BOOT, n_streams))
    for b in range(N_BOOT):
        w[b] = np.bincount(idx[b], minlength=n_streams)
    return w


def ci(x: np.ndarray) -> list[float]:
    return [float(np.nanpercentile(x, 2.5)), float(np.nanpercentile(x, 97.5))]


class Frame:
    """One method at one column (grid row / threshold) over a list of per-stream results."""

    def __init__(self, results: list[dict], method: str, col: int):
        self.n = len(results)
        self.months = np.array([r["clean"]["months"] if "clean" in r else 0.0 for r in results])
        self.fa = np.array(
            [float(r["clean"]["fa"][method][col]) if "clean" in r else 0.0 for r in results]
        )
        owner, det, size, typ, ttd_n, ttd_h, cfg = [], [], [], [], [], [], []
        for s, r in enumerate(results):
            for inj in r["inj"]:
                owner.append(s)
                det.append(bool(inj[method]["det"][col]))
                ttd_n.append(int(inj[method]["ttd_n"][col]))
                ttd_h.append(float(inj[method]["ttd_h"][col]))
                size.append(inj["size"])
                typ.append(inj["type"])
                cfg.append(inj["config_id"])
        self.owner = np.array(owner, dtype=int)
        self.det = np.array(det, dtype=float)
        self.size = np.array(size)
        self.type = np.array(typ)
        self.ttd_n = np.array(ttd_n)
        self.ttd_h = np.array(ttd_h)
        self.cfg = np.array(cfg)

    # point estimates -------------------------------------------------------------------------
    def far(self) -> float:
        return float(self.fa.sum() / self.months.sum())

    def recall(self, mask: np.ndarray | None = None) -> float:
        m = np.ones_like(self.det, dtype=bool) if mask is None else mask
        return float(self.det[m].sum() / max(m.sum(), 1))

    # bootstrap -------------------------------------------------------------------------------
    def far_boot(self, w: np.ndarray) -> np.ndarray:
        return (w @ self.fa) / (w @ self.months)

    def recall_boot(self, w: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
        m = np.ones_like(self.det) if mask is None else mask.astype(float)
        wi = w[:, self.owner]
        num, den = wi @ (self.det * m), wi @ m
        return np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)


def union_frame(a: Frame, b: Frame) -> Frame:
    """Alert if either baseline alerts: detected = a or b, false alerts add (informational)."""
    import copy

    u = copy.copy(a)
    u.det = np.maximum(a.det, b.det)
    u.fa = a.fa + b.fa
    u.ttd_n = np.where(a.det > 0, a.ttd_n, b.ttd_n)
    u.ttd_h = np.where(a.det > 0, a.ttd_h, b.ttd_h)
    return u


def weights_for(frame: Frame) -> np.ndarray:
    return _weights(frame.n)


def summarize(frame: Frame) -> dict:
    """Point estimates + product-bootstrap CIs for one method, with the by-size / by-type tables."""
    w = weights_for(frame)
    out: dict = {
        "false_alerts_per_product_month": {
            "value": frame.far(),
            "ci95": ci(frame.far_boot(w)),
            "alert_episodes": int(frame.fa.sum()),
            "product_months": float(frame.months.sum()),
            "n_products": frame.n,
        },
        "recall": {
            "value": frame.recall(),
            "ci95": ci(frame.recall_boot(w)),
            "n_injections": int(len(frame.det)),
        },
        "recall_by_size": {},
        "recall_by_type": {},
    }
    for s in sorted(set(frame.size.tolist())):
        m = frame.size == s
        out["recall_by_size"][str(s)] = {
            "value": frame.recall(m),
            "ci95": ci(frame.recall_boot(w, m)),
            "n": int(m.sum()),
        }
    for t in sorted(set(frame.type.tolist())):
        m = frame.type == t
        out["recall_by_type"][t] = {
            "value": frame.recall(m),
            "ci95": ci(frame.recall_boot(w, m)),
            "n": int(m.sum()),
        }
    d = frame.det.astype(bool)
    if d.any():
        sizes = frame.size[d].astype(float)
        n, h = frame.ttd_n[d].astype(float), frame.ttd_h[d]
        q = lambda a: [float(np.percentile(a, p)) for p in (25, 50, 75)]  # noqa: E731
        out["time_to_detect"] = {
            "n_detected": int(d.sum()),
            "campaign_reviews_posted_before_alert_q25_q50_q75": q(n),
            "fraction_of_campaign_size_q25_q50_q75": q(n / sizes),
            "hours_from_first_campaign_review_q25_q50_q75": q(h),
        }
    out["min_detectable_size"] = min_detectable(out["recall_by_size"])
    rec = out["recall"]["value"]
    fa = out["false_alerts_per_product_month"]["value"]
    out["alert_precision_modelled"] = {
        name: (rec * rho) / (rec * rho + fa) if (rec * rho + fa) > 0 else None
        for name, rho in RHOS.items()
    }
    return out


def min_detectable(by_size: dict) -> dict:
    """Smallest size whose recall reaches 0.5 / 0.8 (point) and whose CI lower bound reaches it."""
    out = {}
    for level in (0.5, 0.8):
        pt = next((int(s) for s in sorted(by_size, key=int) if by_size[s]["value"] >= level), None)
        cons = next(
            (int(s) for s in sorted(by_size, key=int) if by_size[s]["ci95"][0] >= level), None
        )
        out[f"recall_{level}"] = {"point": pt, "ci_lower_bound": cons}
    return out


def paired_delta(a: Frame, b: Frame) -> dict:
    """Recall(a) - recall(b) with a paired product bootstrap. Frames must cover the same streams
    and the same injections in the same order."""
    assert a.n == b.n and (a.owner == b.owner).all() and (a.cfg == b.cfg).all()
    w = weights_for(a)
    delta = a.recall_boot(w) - b.recall_boot(w)
    return {"value": a.recall() - b.recall(), "ci95": ci(delta)}


# ---- threshold selection (spec section 7) --------------------------------------------------


def select_column(
    tune: dict[str, np.ndarray],
    val: dict[str, np.ndarray],
    budget: float,
    allowed: np.ndarray | None = None,
) -> int | None:
    """tune/val hold per-column 'far' and 'rec' arrays. Best TUNING recall among columns whose
    tuning AND validation false-alert rates are within budget; ties -> lower tuning FAR, then
    lower column index. None if no column meets the budget."""
    ok = (tune["far"] <= budget) & (val["far"] <= budget)
    if allowed is not None:
        ok &= allowed
    cols = np.flatnonzero(ok)
    if len(cols) == 0:
        return None
    order = sorted(cols, key=lambda c: (-tune["rec"][c], tune["far"][c], c))
    return int(order[0])


def column_stats(results: list[dict], method: str, n_cols: int) -> dict[str, np.ndarray]:
    fa = np.sum([r["clean"]["fa"][method] for r in results], axis=0)
    months = sum(r["clean"]["months"] for r in results)
    det = np.sum([inj[method]["det"] for r in results for inj in r["inj"]], axis=0)
    n_inj = sum(len(r["inj"]) for r in results)
    return {
        "far": fa / months,
        "rec": det / max(n_inj, 1),
        "n_inj": np.array([n_inj]),
        "months": np.array([months]),
    }
