"""Moat compare report for the gpt-oss-20b arms (spec Amendment 11, registered before any number here was computed).

Arms: the run-2 encoder, `gpt-oss-20b-local` (zero-shot, Amendment 9) and `gpt-oss-20b-local-fewshot` (Amendment 10), plus the
502 Groq gpt-oss-20b rows as a labelled cross-check that never enters a verdict. Gold = four-judge panel consensus (SILVER).

Rules (see Amendment 11 for the authoritative text):
  * operating point per arm and task = the highest-coverage confidence threshold whose Wilson 95 percent LOWER bound of
    precision among answered items is at least 0.95 (whole tie groups; coverage 0 if none qualifies);
  * MATCHED ids = first 300 sealed-order ids that every compared arm answered and that have gold and an encoder prediction;
  * matched-coverage reading: every arm read at the coverage of the weakest arm with non-zero coverage;
  * paired item bootstrap, seed 42, 2000 resamples, one index set per replicate shared by all arms, operating point recomputed;
  * "A beats B" only if the bootstrap interval excludes 0 in A's favour AND the operating points agree in direction.

This file is part 1 of 2 of the module: the statistics (Wilson, operating point, matched coverage, verdict, paired comparison). Data
loading, the build and the report writer arrive in part 2.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from engine.experiments import selective as SEL
from engine.intent import llm_moat as M

ROOT = M.ROOT
TARGET = 0.95
N_BOOT = 2000
SEED = 42
N_MATCHED_MAX = 300
Z = 1.96
ENC_RUN = "run2"
ARM_ZS, ARM_FS, ARM_ENC, ARM_GROQ = (
    "gpt-oss-20b-local",
    "gpt-oss-20b-local-fewshot",
    "encoder-run2",
    "groq-gpt-oss-20b",
)
PAIRS = [(ARM_ENC, ARM_ZS), (ARM_ENC, ARM_FS), (ARM_FS, ARM_ZS)]
DEFAULTS = {
    "zs": Path("D:/ml-cache/s23/llm_local/openai_gpt-oss-20b__local.jsonl"),
    "fs": Path("D:/ml-cache/s24/llm_local_fs/openai_gpt-oss-20b__local_fewshot.jsonl"),
    "groq": Path("D:/ml-cache/s23/llm/openai_gpt-oss-20b.jsonl"),
}
Arrays = tuple[np.ndarray, np.ndarray]  # (correct bool, confidence float), aligned on ids


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials; (nan, nan) when n == 0."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half) / den, (centre + half) / den


def _groups(correct: np.ndarray, conf: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(answered count, correct count, threshold) at every tie-group boundary, highest confidence first."""
    order = np.argsort(-conf, kind="stable")
    s = conf[order]
    cum = np.cumsum(correct[order].astype(int))
    ends = np.flatnonzero(np.r_[s[1:] != s[:-1], True])
    return ends + 1, cum[ends], s[ends]


def op_point(correct: np.ndarray, conf: np.ndarray, target: float = TARGET) -> dict:
    """Highest-coverage threshold whose Wilson lower bound of precision is >= target (Amendment 11 (a))."""
    n = len(correct)
    if n == 0:
        return {"n": 0, "answered": 0, "coverage": 0.0, "qualifies": False}
    ans, ok, thr = _groups(correct, conf)
    lo = SEL.wilson_lower(ok.astype(float), ans.astype(float))
    q = np.flatnonzero(lo >= target)
    if len(q) == 0:
        return {"n": n, "answered": 0, "coverage": 0.0, "qualifies": False}
    j = int(
        q[-1]
    )  # answered counts increase with j, so the last qualifying group has the highest coverage
    return _point(n, int(ans[j]), int(ok[j]), float(thr[j]), True)


def _point(n: int, answered: int, ok: int, thr: float, qualifies: bool) -> dict:
    lo, hi = wilson(ok, answered)
    return {
        "n": n,
        "answered": answered,
        "threshold": thr,
        "coverage": answered / n,
        "precision": ok / answered,
        "precision_wilson95": [lo, hi],
        "qualifies": qualifies,
    }


def at_count(correct: np.ndarray, conf: np.ndarray, k_max: int) -> dict:
    """Largest whole-tie-group answered set with at most k_max items; the top group alone if it is larger (overshoot)."""
    n = len(correct)
    ans, ok, thr = _groups(correct, conf)
    fit = np.flatnonzero(ans <= k_max)
    j, over = (int(fit[-1]), False) if len(fit) else (0, True)
    p = _point(n, int(ans[j]), int(ok[j]), float(thr[j]), True)
    p["overshoot"] = over
    return p


def matched_ids(
    order: list[str], tables: dict[str, dict[str, tuple[bool, float]]], n_max: int
) -> list[str]:
    """First n_max ids of `order` that every table has (Amendment 11, MATCHED ids)."""
    return [i for i in order[:n_max] if all(i in t for t in tables.values())]


def _stack(ids: list[str], t: dict[str, tuple[bool, float]]) -> Arrays:
    return np.array([t[i][0] for i in ids], dtype=bool), np.array(
        [t[i][1] for i in ids], dtype=float
    )


def verdict(lo: float, hi: float, op_diff: float, a: str = "A", b: str = "B") -> dict:
    """Amendment 11 (d) for a difference A minus B. `op_diff` = A's minus B's operating-point coverage.

    A beats B only if the interval excludes 0 on A's side AND op_diff favours A strictly; the mirror holds for B.
    """
    a_ci, b_ci = lo > 0, hi < 0
    a_op, b_op = op_diff > 0, op_diff < 0
    v = (
        f"{a} beats {b}"
        if (a_ci and a_op)
        else f"{b} beats {a}"
        if (b_ci and b_op)
        else "not separated"
    )
    return {
        "verdict": v,
        "ci_excludes_zero": bool(a_ci or b_ci),
        "op_points_agree": bool((a_ci and a_op) or (b_ci and b_op)),
    }


def compare_task(
    arms: dict[str, Arrays],
    pairs: list[tuple[str, str]],
    n_boot: int = N_BOOT,
    seed: int = SEED,
    verdicts: bool = True,
) -> dict:
    """Operating points, matched-coverage reading and paired bootstrap differences for one task on aligned arrays."""
    n = len(next(iter(arms.values()))[0])
    ops = {a: op_point(c, k) for a, (c, k) in arms.items()}
    acc = {a: _acc(c) for a, (c, _) in arms.items()}
    pos = [o["coverage"] for o in ops.values() if o["coverage"] > 0]
    mc: dict = {"computed": bool(pos)}
    if pos:
        cstar = min(pos)
        kmax = math.ceil(cstar * n - 1e-9)
        mc.update(
            {
                "target_coverage": cstar,
                "k_max": kmax,
                "arms": {a: at_count(c, k, kmax) for a, (c, k) in arms.items()},
            }
        )
    rng = np.random.default_rng(seed)
    cov_b = {a: np.empty(n_boot) for a in arms}
    acc_b = {a: np.empty(n_boot) for a in arms}
    for b in range(n_boot):
        ix = rng.integers(0, n, n)
        for a, (c, k) in arms.items():
            cov_b[a][b] = op_point(c[ix], k[ix])["coverage"]
            acc_b[a][b] = c[ix].mean()
    out_pairs = {}
    for a, b in pairs:
        row = {}
        for name, boot, pt in (("coverage_at_op_point", cov_b, ops), ("accuracy", acc_b, None)):
            d = boot[a] - boot[b]
            lo, hi = (float(x) for x in np.percentile(d, [2.5, 97.5]))
            diff = (
                (pt[a]["coverage"] - pt[b]["coverage"]) if pt else acc[a]["value"] - acc[b]["value"]
            )
            row[name] = {"diff": diff, "ci95": [lo, hi]}
            if verdicts:
                row[name].update(verdict(lo, hi, ops[a]["coverage"] - ops[b]["coverage"], a, b))
        out_pairs[f"{a} minus {b}"] = row
    return {
        "n": n,
        "arms": {a: {"accuracy": acc[a], "op_point": ops[a]} for a in arms},
        "matched_coverage": mc,
        "pairs": out_pairs,
    }


def _acc(c: np.ndarray) -> dict:
    k, n = int(c.sum()), len(c)
    lo, hi = wilson(k, n)
    return {"value": k / n if n else float("nan"), "correct": k, "n": n, "wilson95": [lo, hi]}
