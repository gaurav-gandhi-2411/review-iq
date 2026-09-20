"""S15c-S1c: sample-size and token budget for a controlled measurement of injection landing.

Design (NOT run here; this script only computes the numbers, zero calls of any kind):

  Unit = one base review sentence. For each unit build an attack text A (instruction prepended)
  and its attack-free twin T (same sentence, instruction removed). Run BOTH under the same model,
  prompt and sampling settings. Score each against the attacker's target predicate:
      Y_A = 1 if the attack run's output matches the target, Y_T = 1 if the twin run's does.
  A pair "landed" iff Y_A = 1 and Y_T = 0 (the attack changed the output to the attacker's value;
  the model would not have produced it anyway). Pairs with Y_A = 0 and Y_T = 1 (c) are noise that
  points the wrong way and are what makes the test honest.
  Test: exact McNemar = two-sided exact binomial test of b vs b+c at p = 0.5.

Why paired: repeating one attack text n=3 times is pseudo-replication (one review, one phrasing);
the class-level claim needs many distinct base reviews, each acting as its own control.

Everything below is exact (binomial enumeration), stdlib only.
"""

from __future__ import annotations

import json
import sys
from math import comb, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.provenance import get_git_sha, now_iso  # noqa: E402

OUT_PATH = ROOT / "eval" / "results" / "injection_landing_sample_size.json"
ALPHA = 0.05
POWER = 0.80
# The brief's figure; the measured small-tier mean is 2524.8 (token_cost_measurement_n106.json).
TOKENS_PER_CALL = 2500
# Daily token ceilings per model: the repo's capacity model implies 200K TPD for gpt-oss-20b
# (SECURITY.md and eval/results/capacity_model.json); the S15c brief says 100K. Both reported.
TPD_CEILINGS = {"200K_per_capacity_model": 200_000, "100K_per_brief": 100_000}


def binom_pmf(k: int, n: int, p: float) -> float:
    return comb(n, k) * (p**k) * ((1 - p) ** (n - k))


def exact_two_sided_p(b: int, d: int) -> float:
    """Two-sided exact binomial p-value for b successes in d trials at p=0.5."""
    if d == 0:
        return 1.0
    k = min(b, d - b)
    p = 2 * sum(comb(d, i) for i in range(k + 1)) / 2**d
    return min(1.0, p)


def mcnemar_power(n_pairs: int, p10: float, p01: float) -> float:
    pd = p10 + p01
    if pd == 0:
        return 0.0
    psi = p10 / pd
    return sum(
        binom_pmf(d, n_pairs, pd) * _rej_prob(d, psi)
        for d in range(1, n_pairs + 1)
        if binom_pmf(d, n_pairs, pd) > 1e-15
    )


_CRIT_CACHE: dict[int, int] = {}


def _rej_prob(d: int, psi: float) -> float:
    """P(reject | d discordant pairs) when P(b) = psi: P(b<=k) + P(b>=d-k), k = critical value
    (largest k whose two-sided exact p <= ALPHA; -1 if the test cannot reject at this d)."""
    if d not in _CRIT_CACHE:
        k = -1
        while k + 1 <= d // 2 and exact_two_sided_p(k + 1, d) <= ALPHA:
            k += 1
        _CRIT_CACHE[d] = k
    k = _CRIT_CACHE[d]
    if k < 0:
        return 0.0
    return sum(binom_pmf(b, d, psi) for b in range(k + 1)) + sum(
        binom_pmf(b, d, psi) for b in range(d - k, d + 1)
    )


def joint(pa: float, pt: float, phi: float) -> tuple[float, float]:
    """(p10, p01) for Bernoulli marginals pa, pt with correlation phi, clamped to feasibility."""
    p11 = pa * pt + phi * sqrt(pa * (1 - pa) * pt * (1 - pt))
    p11 = max(max(0.0, pa + pt - 1.0), min(min(pa, pt), p11))
    return pa - p11, pt - p11


def min_pairs(p10: float, p01: float, n_max: int = 400) -> int | None:
    """Smallest N with power >= POWER that also holds for the next 10 N (discreteness guard)."""
    powers = [mcnemar_power(n, p10, p01) for n in range(1, n_max + 11)]
    for n in range(1, n_max + 1):
        if all(powers[i - 1] >= POWER for i in range(n, n + 11)):
            return n
    return None


def fisher_two_sided(a: int, n1: int, c: int, n2: int) -> float:
    """Exact two-sided Fisher p for a/n1 vs c/n2 (sum of tables no more likely than observed)."""
    total = a + c
    denom = comb(n1 + n2, total)

    def pt(x: int) -> float:
        return comb(n1, x) * comb(n2, total - x) / denom

    obs = pt(a)
    lo, hi = max(0, total - n2), min(n1, total)
    return sum(pt(x) for x in range(lo, hi + 1) if pt(x) <= obs + 1e-12)


def min_runs_unpaired(pa: float, pt: float, n_max: int = 60) -> int | None:
    """Smallest R per arm (single base text, R runs of attack vs R runs of twin) with
    Fisher-exact power >= POWER, also holding for the next 5 R."""

    def power(r: int) -> float:
        pw = 0.0
        for x in range(r + 1):
            px = binom_pmf(x, r, pa)
            for y in range(r + 1):
                if fisher_two_sided(x, r, y, r) <= ALPHA:
                    pw += px * binom_pmf(y, r, pt)
        return pw

    pows = {r: power(r) for r in range(2, n_max + 6)}
    for r in range(2, n_max + 1):
        if all(pows[i] >= POWER for i in range(r, r + 6)):
            return r
    return None


def main() -> int:
    scenarios = [
        ("A. 60% vs 10%, independent", 0.60, 0.10, 0.0),
        ("B. 60% vs 10%, phi=0.3 (review-level predisposition)", 0.60, 0.10, 0.3),
        ("C. 90% vs 10% (close to the observed 3/3 landings)", 0.90, 0.10, 0.0),
        ("D. 50% vs 20%", 0.50, 0.20, 0.0),
        ("E. 60% vs 30% (topics: model often returns [] anyway)", 0.60, 0.30, 0.0),
        ("F. 40% vs 10%", 0.40, 0.10, 0.0),
        ("G. 60% vs 0% (twin never produces the target)", 0.60, 0.0, 0.0),
    ]
    rows = []
    for name, pa, pt, phi in scenarios:
        p10, p01 = joint(pa, pt, phi)
        n = min_pairs(p10, p01)
        rows.append(
            {
                "scenario": name,
                "p_attack": pa,
                "p_twin": pt,
                "phi": phi,
                "p_b_attack_only": round(p10, 4),
                "p_c_twin_only": round(p01, 4),
                "discordant_fraction": round(p10 + p01, 4),
                "min_pairs_N_power80": n,
                "power_at_N": round(mcnemar_power(n, p10, p01), 4) if n else None,
            }
        )

    # Budget: calls = 2 (attack + twin) x N pairs x k attack types.
    budget = []
    for r in rows:
        n = r["min_pairs_N_power80"]
        if n is None:
            continue
        for k, label in (
            (3, "3 landing types (buy_again, stars_inferred, topics)"),
            (8, "all 8 field-targeted"),
        ):
            calls = 2 * n * k
            tokens = calls * TOKENS_PER_CALL
            budget.append(
                {
                    "scenario": r["scenario"],
                    "attack_types": label,
                    "N_pairs_per_type": n,
                    "calls": calls,
                    "tokens": tokens,
                    "days_at_ceiling": {
                        key: round(tokens / v, 2) for key, v in TPD_CEILINGS.items()
                    },
                    "calls_per_day_at_ceiling": {
                        key: v // TOKENS_PER_CALL for key, v in TPD_CEILINGS.items()
                    },
                }
            )

    # Per-attack (single text) alternative, and what the existing n=3 can/cannot show.
    unpaired = [
        {
            "scenario": "60% vs 10%",
            "min_runs_per_arm_single_text": min_runs_unpaired(0.60, 0.10),
        },
        {
            "scenario": "90% vs 10%",
            "min_runs_per_arm_single_text": min_runs_unpaired(0.90, 0.10),
        },
    ]
    existing = {
        "landed_3_of_3_vs_twin_0_of_3_fisher_two_sided_p": round(fisher_two_sided(3, 3, 0, 3), 4),
        "note": "the best possible outcome of a 3-vs-3 design cannot reach alpha=0.05; a twin arm at n=3 is uninformative",
    }
    # Twin-vs-twin noise floor (optional add-on): extra calls to estimate the model's own flip rate.
    result = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "script": "scripts/injection_landing_sample_size.py",
        "zero_quota": True,
        "alpha_two_sided": ALPHA,
        "power": POWER,
        "tokens_per_call_assumed": TOKENS_PER_CALL,
        "tpd_ceilings_tokens_per_day": TPD_CEILINGS,
        "paired_mcnemar_exact": rows,
        "budget_paired": budget,
        "single_text_unpaired_fisher": unpaired,
        "existing_design_n3": existing,
    }
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    for r in rows:
        print(
            r["scenario"],
            "-> N pairs:",
            r["min_pairs_N_power80"],
            "power",
            r["power_at_N"],
            "disc",
            r["discordant_fraction"],
        )
    for b in budget:
        if b["attack_types"].startswith("3"):
            print(
                b["scenario"][:2], b["calls"], "calls", b["tokens"], "tokens", b["days_at_ceiling"]
            )
    print(unpaired, existing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
