"""Harness correctness tests: the evaluated rule must BE the shipped rule, excerpts must equal
full-stream evaluation, and the selection / bootstrap / split mechanics must behave."""

from __future__ import annotations

import random

import numpy as np
from app.core.detectors.campaign import is_alert
from app.core.detectors.campaign_signals import CampaignParams, ProductStream
from benchmark.campaign_eval.data import Stream, split_keys
from benchmark.campaign_eval.evaluate import (
    EVAL_PARAMS,
    excerpt_stream,
    injection_time,
)
from benchmark.campaign_eval.generator import CampaignConfig
from benchmark.campaign_eval.grid import (
    COOLDOWN_S,
    build_grid,
    detector_alerts,
    injected_outcomes,
    open_alerts,
)
from benchmark.campaign_eval.trace import SIMS, WINDOWS, trace_stream

VOCAB = [f"word{k}x" for k in range(400)]
DAY = 86400.0
T0 = 1_700_000_000.0


def _stream(n: int = 500, days: float = 500.0, seed: int = 1) -> Stream:
    rng = random.Random(seed)
    ts = np.array(sorted(T0 + rng.uniform(0, days * DAY) for _ in range(n)))
    return Stream(
        key="t:S",
        corpus="t",
        ts=ts,
        rating=np.array([rng.choice([1, 4, 5, 5, 5]) for _ in range(n)], dtype=np.int8),
        text=[" ".join(rng.choice(VOCAB) for _ in range(9)) for _ in range(n)],
    )


def test_grid_has_768_rows_and_params_roundtrip() -> None:
    g = build_grid()
    assert len(g) == 768
    p = g.params(0)
    assert p["similarity"] in SIMS and p["k_min"] in (3, 4)


def test_vectorised_rule_equals_production_is_alert_point_for_point() -> None:
    s = _stream()
    cfg = CampaignConfig(12, 6, "near-identical", "all-1")
    t_inj, seed = injection_time(s, cfg)
    ps, camp_mask, _, points = excerpt_stream(s, cfg, t_inj, seed, {})
    tr = trace_stream(ps, points, camp_mask)
    g = build_grid()
    rng = np.random.default_rng(0)
    sel = np.sort(rng.choice(len(g), size=24, replace=False))
    sel = np.union1d(sel, [int(np.flatnonzero(g.use_m)[0])])
    any_v, attr_v = detector_alerts(tr, g, sel)
    cc = np.concatenate([[0], np.cumsum(camp_mask)])
    checked = fired = 0
    for row, col in enumerate(sel):
        pr = g.params(int(col))
        params = CampaignParams(
            similarity=pr["similarity"], k_min=pr["k_min"], theta_burst=pr["theta_burst"],
            theta_rating=pr["theta_rating"], strong_multiplier=pr["strong_multiplier"],
            use_mismatch=pr["use_mismatch"], store_min_similarity=min(SIMS),
        )  # fmt: skip
        for p, i in enumerate(points):
            ok_prod, first_attr = False, 0
            for w in WINDOWS:
                ev = ps.evidence_at(i, w)
                if ev is None:
                    continue
                # production evidence is computed at EVAL_PARAMS.similarity; recompute the group
                # size at this row's similarity exactly as the harness does
                k = ps.largest_cluster(i - ev.n + 1, i, pr["similarity"])[0]
                from dataclasses import replace

                ok, _ = is_alert(replace(ev, template_k=k), params)
                if ok:
                    ok_prod = True
                    first_attr = int(cc[i + 1] - cc[i - ev.n + 1])
                    break
            checked += 1
            fired += ok_prod
            assert bool(any_v[row, p]) == ok_prod, (int(col), p)
            if ok_prod:
                assert int(attr_v[row, p]) == first_attr
    assert checked > 500 and fired > 0, "comparison must include real alerts to be meaningful"
    assert EVAL_PARAMS.store_min_similarity == min(SIMS)


def test_excerpt_evidence_equals_full_stream_evidence() -> None:
    s = _stream(n=800, days=600.0, seed=5)
    cfg = CampaignConfig(5, 24, "independent", "all-5")
    t_inj, seed = injection_time(s, cfg)
    ps_x, mask, _, points = excerpt_stream(s, cfg, t_inj, seed, {})
    # same stream WITHOUT the campaign: excerpt vs full must agree at every clean point
    from benchmark.campaign_eval.evaluate import EXCERPT_AFTER_S, EXCERPT_BEFORE_S

    lo = np.searchsorted(s.ts, t_inj - EXCERPT_BEFORE_S)
    hi = np.searchsorted(s.ts, t_inj + 24 * 3600 + EXCERPT_AFTER_S, side="right")
    ex = ProductStream(s.reviews(int(lo), int(hi)), EVAL_PARAMS)
    full = ProductStream(s.reviews(), EVAL_PARAMS)
    index_full = {r.review_id: i for i, r in enumerate(full.reviews)}
    n_cmp = 0
    for i, r in enumerate(ex.reviews):
        if ex.ts[i] < t_inj - 7 * DAY:
            continue
        for w in WINDOWS:
            a, b = ex.evidence_at(i, w), full.evidence_at(index_full[r.review_id], w)
            assert (a is None) == (b is None)
            if a is not None:
                n_cmp += 1
                assert (a.n, a.burst, a.rating_z, a.template_k) == (
                    b.n,
                    b.burst,
                    b.rating_z,
                    b.template_k,
                )
    assert n_cmp > 20


def test_open_alerts_applies_the_seven_day_cooldown() -> None:
    t = np.array([0.0, 1 * DAY, 6 * DAY, 7 * DAY, 7.5 * DAY, 15 * DAY])
    assert open_alerts(t, np.arange(6)) == [0, 3, 5]
    assert COOLDOWN_S == 7 * DAY


def test_injected_outcomes_require_interval_and_attribution() -> None:
    from benchmark.campaign_eval.trace import Trace

    t = np.array([0.0, 100.0, 200.0, 100000.0])
    z = np.zeros((4, 4))
    tr = Trace(
        t,
        z,
        np.zeros((4, 4, 4), dtype=np.int16),
        z,
        z,
        z.astype(np.int32),
        z[:, 0],
        z[:, 0].astype(np.int32),
        z[:, 0],
        z[:, 0].astype(np.int32),
    )
    camp_ts = np.array([50.0, 150.0, 250.0])
    any_alert = np.array(
        [[False, True, False, False], [False, True, False, False], [False, False, False, True]]
    )
    attr = np.array([[0, 3, 0, 0], [0, 2, 0, 0], [0, 0, 0, 5]])
    det, ttd_n, ttd_h = injected_outcomes(tr, any_alert, attr, camp_ts)
    assert det.tolist() == [True, False, False]  # attribution < 3, then outside [first, last+24h]
    assert ttd_n[0] == 1 and abs(ttd_h[0] - 50 / 3600) < 1e-9


def test_split_keys_is_seeded_disjoint_and_proportional() -> None:
    keys = {"a": [f"a{i}" for i in range(100)], "b": [f"b{i}" for i in range(50)]}
    s1, s2 = split_keys(keys), split_keys(keys)
    assert s1 == s2
    assert [len(s1[k]) for k in ("tuning", "validation", "sealed")] == [75, 30, 45]
    all_keys = s1["tuning"] + s1["validation"] + s1["sealed"]
    assert len(all_keys) == len(set(all_keys)) == 150
