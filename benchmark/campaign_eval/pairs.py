"""Pairwise evaluation of the template (near-duplicate text) signal, separate from the alert
evaluation (spec section 5).

Positives (SYNTHETIC): pairs of reviews from one generated template at the paraphrased and
near-identical levels, same polarity group. Negatives (real corpus, ASSUMED ORGANIC): (i) random
same-product pairs within 7 days, (ii) hard negatives, the highest-similarity real pairs inside
each product, (iii) short generic pairs (under 6 words), scored with and without the eligibility
gate. Precision is computed at a fixed 1 positive : 10 negatives weighting over the random
negatives (i); rates carry Wilson 95% intervals (pairs inside one product are correlated, so the
intervals are optimistic and are labelled so).
"""

from __future__ import annotations

import itertools
import math
import random
from datetime import UTC, datetime

import numpy as np
from app.core.detectors.campaign_signals import (
    CampaignParams,
    ProductStream,
    _shingles,
    jaccard,
)

from benchmark.campaign_eval.data import Stream
from benchmark.campaign_eval.generator import CampaignConfig, generate_campaign
from benchmark.campaign_eval.trace import SIMS

GATED = CampaignParams()
UNGATED = CampaignParams(min_words_template=1, min_chars_template=1)
WINDOW_S = 168 * 3600.0


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def positive_pairs(
    configs: list[CampaignConfig], max_per_config: int = 200
) -> dict[str, list[tuple]]:
    """level -> list of (shingles_a, shingles_b) with eligibility applied by the caller."""
    out: dict[str, list[tuple]] = {"paraphrased": [], "near-identical": []}
    rng = random.Random(42)
    for cfg in configs:
        if cfg.similarity not in out:
            continue
        camp = generate_campaign(cfg, datetime(2024, 1, 1, tzinfo=UTC), 7000 + cfg.size, "P")
        pairs = [
            (a, b)
            for a, b in itertools.combinations(camp, 2)
            if (a.rating == 5) == (b.rating == 5)  # same polarity group = same template
        ]
        rng.shuffle(pairs)
        for a, b in pairs[:max_per_config]:
            out[cfg.similarity].append((a.text, b.text))
    return out


def _rate(sims: list[float], s: float) -> tuple[int, int]:
    return sum(x >= s for x in sims), len(sims)


def _pair_sim(t1: str, t2: str, p: CampaignParams) -> float:
    a, b = _shingles(t1, p), _shingles(t2, p)
    return (
        jaccard(a, b) if a is not None and b is not None else -1.0
    )  # -1: ineligible, never matches


def real_negatives(streams: list[Stream], n_random: int = 4000, n_short: int = 2000) -> dict:
    rng = np.random.default_rng(42)
    rand_pairs: list[tuple[str, str]] = []
    short_pairs: list[tuple[str, str]] = []
    hard: list[tuple[float, str, str]] = []
    for st in streams:
        n = len(st)
        short_idx = [i for i, t in enumerate(st.text) if len(t.split()) < 6]
        for _ in range(max(1, n_random // len(streams))):
            i = int(rng.integers(0, n))
            lo = int(np.searchsorted(st.ts, st.ts[i] - WINDOW_S))
            hi = int(np.searchsorted(st.ts, st.ts[i] + WINDOW_S, side="right"))
            if hi - lo > 1:
                j = int(rng.integers(lo, hi))
                if j != i:
                    rand_pairs.append((st.text[i], st.text[j]))
        for _ in range(max(1, n_short // len(streams))):
            if len(short_idx) < 2:
                break
            i = int(rng.choice(short_idx))
            near = [j for j in short_idx if j != i and abs(st.ts[j] - st.ts[i]) < WINDOW_S]
            if near:
                short_pairs.append((st.text[i], st.text[int(rng.choice(near))]))
        ps = ProductStream(st.reviews(), CampaignParams(store_min_similarity=min(SIMS)))
        top = sorted(((sim, i, j) for j, nb in enumerate(ps.nbrs) for i, sim in nb), reverse=True)[
            :10
        ]
        hard += [(sim, ps.reviews[i].text, ps.reviews[j].text) for sim, i, j in top]
    return {"random": rand_pairs, "short": short_pairs, "hard": hard}


def evaluate_pairs(
    streams: list[Stream], configs: list[CampaignConfig], s_values: list[float]
) -> dict:
    pos = positive_pairs(configs)
    neg = real_negatives(streams)
    out: dict = {
        "label": "positives SYNTHETIC; negatives real corpus pairs (assumed organic, may include real duplicates)",
        "n": {
            "positive_paraphrased": len(pos["paraphrased"]),
            "positive_near_identical": len(pos["near-identical"]),
            "negative_random": len(neg["random"]),
            "negative_short_generic": len(neg["short"]),
            "negative_hard_top_similarity": len(neg["hard"]),
        },
        "by_similarity_threshold": {},
        "precision_weighting": "1 positive : 10 negatives over the random-negative false-positive rate",
    }
    sims = {
        "pos_par": [_pair_sim(a, b, GATED) for a, b in pos["paraphrased"]],
        "pos_nid": [_pair_sim(a, b, GATED) for a, b in pos["near-identical"]],
        "rand": [_pair_sim(a, b, GATED) for a, b in neg["random"]],
        "short_gated": [_pair_sim(a, b, GATED) for a, b in neg["short"]],
        "short_ungated": [_pair_sim(a, b, UNGATED) for a, b in neg["short"]],
        "hard": [_pair_sim(a, b, GATED) for _, a, b in neg["hard"]],
    }
    out["median_similarity"] = {
        k: float(np.median([x for x in v if x >= 0])) if any(x >= 0 for x in v) else None
        for k, v in sims.items()
    }
    for s in s_values:
        res: dict = {}
        for name, key in (("recall_paraphrased", "pos_par"), ("recall_near_identical", "pos_nid")):
            k, n = _rate(sims[key], s)
            res[name] = {"value": k / n if n else None, "ci95_wilson": wilson(k, n), "n": n}
        pooled_k = sum(_rate(sims[k], s)[0] for k in ("pos_par", "pos_nid"))
        pooled_n = len(sims["pos_par"]) + len(sims["pos_nid"])
        tpr = pooled_k / pooled_n if pooled_n else 0.0
        res["recall_pooled"] = {
            "value": tpr,
            "ci95_wilson": wilson(pooled_k, pooled_n),
            "n": pooled_n,
        }
        for name, key in (
            ("false_positive_random_pairs", "rand"),
            ("false_positive_hard_negatives", "hard"),
            ("false_positive_short_generic_gated", "short_gated"),
            ("false_positive_short_generic_UNGATED", "short_ungated"),
        ):
            k, n = _rate(sims[key], s)
            res[name] = {
                "value": k / n if n else None,
                "ci95_wilson": wilson(k, n),
                "count": k,
                "n": n,
            }
        fpr = res["false_positive_random_pairs"]["value"] or 0.0
        res["precision_at_1_to_10"] = tpr / (tpr + 10 * fpr) if (tpr + 10 * fpr) > 0 else None
        out["by_similarity_threshold"][str(s)] = res
    return out
