"""Per-stream evaluation: clean false alerts and injected-campaign outcomes for the detector grid
and both baselines. Deterministic (seed 42); no network; no database."""

from __future__ import annotations

import pickle
import zlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from app.core.detectors.campaign_signals import CampaignParams, ProductStream

from benchmark.campaign_eval.data import Stream
from benchmark.campaign_eval.generator import (
    CampaignConfig,
    generate_campaign,
    injection_seed,
    utc,
)
from benchmark.campaign_eval.grid import (
    B1_THETAS,
    B2_THETAS,
    Grid,
    baseline_alerts,
    build_grid,
    clean_false_alerts,
    detector_alerts,
    injected_outcomes,
)
from benchmark.campaign_eval.trace import SIMS, Trace, trace_stream

DAY_S = 86400.0
HOUR_S = 3600.0
MONTH_S = 30.4375 * DAY_S
EXCERPT_BEFORE_S = 195 * DAY_S  # 180d history + 7d widest window + 7d cooldown pre-roll + 1d slack
EXCERPT_AFTER_S = 8 * DAY_S
PRE_ROLL_S = 7 * DAY_S
TAIL_S = 14 * DAY_S  # injections leave >= 14 days of stream after the campaign
CHUNK = 64
EVAL_PARAMS = CampaignParams(store_min_similarity=min(SIMS))
CACHE_DIR = Path(r"D:\ml-cache\campaign_eval")


@dataclass
class Selection:
    """Which grid rows / baseline thresholds to evaluate (the sealed run passes only frozen ones)."""

    det: np.ndarray
    b1: np.ndarray
    b2: np.ndarray


def full_selection() -> Selection:
    return Selection(np.arange(len(build_grid())), B1_THETAS, B2_THETAS)


def _alerts_for_all(
    tr: Trace, grid: Grid, sel: Selection
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    parts = [
        detector_alerts(tr, grid, sel.det[i : i + CHUNK]) for i in range(0, len(sel.det), CHUNK)
    ]
    if parts:
        out["det"] = (np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts]))
    if len(sel.b1):
        out["b1"] = baseline_alerts(tr, "b1", sel.b1)
    if len(sel.b2):
        out["b2"] = baseline_alerts(tr, "b2", sel.b2)
    return out


def clean_points(stream: Stream) -> tuple[list[int], float]:
    """Evaluation points (after warm-up) and the evaluated span in months."""
    t_warm = stream.warmup_time()
    pts = [i for i in range(len(stream)) if i >= 100 and stream.ts[i] >= t_warm]
    months = (stream.ts[-1] - t_warm) / MONTH_S
    return pts, months


def evaluate_clean(stream: Stream, sel: Selection, cache: dict) -> dict:
    ps = ProductStream(stream.reviews(), EVAL_PARAMS, shingle_cache=cache)
    pts, months = clean_points(stream)
    tr = trace_stream(ps, pts)
    grid = build_grid()
    fa = {k: clean_false_alerts(tr, a) for k, (a, _) in _alerts_for_all(tr, grid, sel).items()}
    return {"months": months, "fa": fa, "n_points": len(pts)}


def injection_time(stream: Stream, cfg: CampaignConfig) -> tuple[float, int]:
    seed = injection_seed(stream.key, cfg.config_id)
    lo = stream.warmup_time()
    hi = float(stream.ts[-1]) - TAIL_S - cfg.duration_h * HOUR_S
    return float(np.random.default_rng(seed).uniform(lo, hi)), seed


def excerpt_stream(
    stream: Stream, cfg: CampaignConfig, t_inj: float, seed: int, cache: dict
) -> tuple[ProductStream, np.ndarray, np.ndarray, list[int]]:
    lo = bisect_left(stream.ts, t_inj - EXCERPT_BEFORE_S)
    hi = bisect_right(stream.ts, t_inj + cfg.duration_h * HOUR_S + EXCERPT_AFTER_S)
    camp = generate_campaign(cfg, utc(t_inj), seed, product_id=stream.key)
    ps = ProductStream(stream.reviews(lo, hi) + camp, EVAL_PARAMS, shingle_cache=cache)
    camp_mask = np.array([r.review_id.startswith("inj-") for r in ps.reviews])
    camp_ts = np.array(sorted(r.timestamp.timestamp() for r in camp))
    points = [i for i, t in enumerate(ps.ts) if t >= t_inj - PRE_ROLL_S]
    return ps, camp_mask, camp_ts, points


def evaluate_injection(stream: Stream, cfg: CampaignConfig, sel: Selection, cache: dict) -> dict:
    t_inj, seed = injection_time(stream, cfg)
    ps, camp_mask, camp_ts, points = excerpt_stream(stream, cfg, t_inj, seed, cache)
    tr = trace_stream(ps, points, camp_mask)
    grid = build_grid()
    out: dict = {"config_id": cfg.config_id, "size": cfg.size, "type": cfg.type_id, "t_inj": t_inj}
    for name, (any_alert, attr) in _alerts_for_all(tr, grid, sel).items():
        det, ttd_n, ttd_h = injected_outcomes(tr, any_alert, attr, camp_ts)
        out[name] = {"det": det, "ttd_n": ttd_n, "ttd_h": ttd_h}
    return out


def evaluate_stream(
    stream: Stream, cfgs: list[CampaignConfig], sel: Selection, do_clean: bool = True
) -> dict:
    cache: dict = {}
    res: dict = {"key": stream.key, "corpus": stream.corpus}
    if do_clean:
        res["clean"] = evaluate_clean(stream, sel, cache)
    res["inj"] = [evaluate_injection(stream, c, sel, cache) for c in cfgs]
    return res


def sample_tuning_configs(
    key: str, tuning: list[CampaignConfig], k: int = 20
) -> list[CampaignConfig]:
    """Spec section 4: each tuning product is paired with 20 seeded tuning configurations."""
    rng = np.random.default_rng(42 + zlib.crc32(key.encode()))
    idx = rng.choice(len(tuning), size=k, replace=False)
    return [tuning[int(i)] for i in sorted(idx)]


def save_pickle(obj: object, name: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with (CACHE_DIR / name).open("wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_pickle(name: str) -> object:
    with (CACHE_DIR / name).open("rb") as f:
        return pickle.load(f)  # noqa: S301  # our own cache files written by save_pickle
