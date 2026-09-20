"""C2c (Session 15c): what the corrected free-text scorers change on the n=106 held-out set.

Zero quota: reads the RECORDED predictions in eval/results/held_out_scoring_v2.json (produced by
cassette replay) and the held-out gold fixtures, and scores each prediction twice -- strict
(pre-15c exact-string behaviour, `score_fixture(strict=True)`) and corrected -- so every delta
is attributable to eval/free_text_scoring.py and nothing else. Predictions never change.

Attribution ladder for `product` (each step is a strictly larger set of rules):
  strict            exact string match, case-insensitive (the published behaviour)
  null_only         + "no product named" strings canonicalized to null on both sides
  corrected         + case/punctuation/plural/order/spacing normalization of named products

Reports, per condition (as_deployed / language_forced) and per field: strict mean, corrected
mean, paired-bootstrap 95% CI of the per-fixture delta (10,000 resamples, seed 42), and the
exact list of fixtures whose score changed with old -> new values, so the change is auditable
rather than trusted. A NEGATIVE flip (a score that went DOWN) would be a red flag for the
scorer and is counted separately.

Usage: uv run python eval/measure_scorer_delta.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.free_text_scoring import SCORER_VERSION, canonical_product  # noqa: E402
from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.runner import _exact_score, score_fixture  # noqa: E402

HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
PREDICTIONS = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
OUT_PATH = ROOT / "eval" / "results" / "scorer_delta_n106.json"
N_RESAMPLES = 10_000
SEED = 42  # rule 40; matches eval/bootstrap.py's pinned default


def paired_delta_ci(deltas: list[float]) -> tuple[float, float]:
    """Percentile-bootstrap 95% CI of the mean per-fixture delta (NOT clamped to [0, 1])."""
    rng = random.Random(SEED)  # noqa: S311 -- statistical resampling, not security
    n = len(deltas)
    means = sorted(mean(rng.choices(deltas, k=n)) for _ in range(N_RESAMPLES))
    return means[int(0.025 * N_RESAMPLES)], means[int(0.975 * N_RESAMPLES) - 1]


def _null_only_product(pred: Any, gold: Any) -> float:
    p_null, g_null = canonical_product(pred) is None, canonical_product(gold) is None
    if p_null and g_null:
        return 1.0
    if p_null or g_null:
        return 0.0
    return _exact_score(pred, gold)


def _gold() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(HELD_OUT_DIR.glob("*.json")):
        if path.name.startswith("."):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "review_text" in data:
            out[data["id"]] = data
    return out


def main() -> int:
    gold = _gold()
    payload = json.loads(PREDICTIONS.read_text(encoding="utf-8"))
    records = payload["records"]
    result: dict[str, Any] = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "scorer_version": SCORER_VERSION,
        "predictions_source": str(PREDICTIONS.relative_to(ROOT)).replace("\\", "/"),
        "predictions_git_sha": payload.get("git_sha"),
        "n": len(records),
        "conditions": {},
    }

    for cond in ("as_deployed", "language_forced"):
        strict_scores: dict[str, dict[str, float]] = {}
        new_scores: dict[str, dict[str, float]] = {}
        null_only: dict[str, float] = {}
        for rec in records:
            fx = gold[rec["id"]]
            pred = rec[cond]["predicted"]
            strict_scores[rec["id"]] = {
                r.field: r.score for r in score_fixture(fx, pred, strict=True)
            }
            new_scores[rec["id"]] = {r.field: r.score for r in score_fixture(fx, pred)}
            null_only[rec["id"]] = _null_only_product(
                pred.get("product"), fx["ground_truth"].get("product")
            )
            # Self-checks: the recorded artifact must have been produced by exactly this
            # scorer (corrected re-score == recorded scores), and its recorded old-comparator
            # overall must equal the strict re-score. Either failing means the artifact and
            # this code disagree about what "strict" and "corrected" mean.
            assert new_scores[rec["id"]] == rec[cond]["field_scores"], rec["id"]
            assert (
                abs(mean(strict_scores[rec["id"]].values()) - rec[cond]["overall_score_strict"])
                < 1e-12
            ), rec["id"]

        ids = [r["id"] for r in records]
        fields = list(next(iter(strict_scores.values())))
        per_field: dict[str, Any] = {}
        for f in fields:
            old = [strict_scores[i][f] for i in ids]
            new = [new_scores[i][f] for i in ids]
            deltas = [b - a for a, b in zip(old, new, strict=True)]
            lo, hi = paired_delta_ci(deltas)
            per_field[f] = {
                "strict_mean": mean(old),
                "corrected_mean": mean(new),
                "delta": mean(deltas),
                "delta_ci95": [lo, hi],
                "n_up": sum(1 for d in deltas if d > 0),
                "n_down": sum(1 for d in deltas if d < 0),
                "flips": [
                    {"id": i, "old": a, "new": b}
                    for i, a, b in zip(ids, old, new, strict=True)
                    if a != b
                ],
            }
        old_overall = [mean(strict_scores[i].values()) for i in ids]
        new_overall = [mean(new_scores[i].values()) for i in ids]
        d_overall = [b - a for a, b in zip(old_overall, new_overall, strict=True)]
        lo, hi = paired_delta_ci(d_overall)
        prod_old = [strict_scores[i]["product"] for i in ids]
        prod_null = [null_only[i] for i in ids]
        prod_new = [new_scores[i]["product"] for i in ids]
        result["conditions"][cond] = {
            "overall": {
                "strict_mean": mean(old_overall),
                "corrected_mean": mean(new_overall),
                "delta": mean(d_overall),
                "delta_ci95": [lo, hi],
            },
            "product_attribution": {
                "strict": mean(prod_old),
                "null_only": mean(prod_null),
                "corrected": mean(prod_new),
            },
            "per_field": per_field,
        }

    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for cond, c in result["conditions"].items():
        o = c["overall"]
        print(
            f"[{cond}] overall {o['strict_mean']:.4f} -> {o['corrected_mean']:.4f} "
            f"(delta {o['delta']:+.4f}, 95% CI [{o['delta_ci95'][0]:+.4f}, {o['delta_ci95'][1]:+.4f}])"
        )
        pa = c["product_attribution"]
        print(
            f"  product: strict {pa['strict']:.3f} -> null_only {pa['null_only']:.3f} "
            f"-> corrected {pa['corrected']:.3f}"
        )
        for f, v in c["per_field"].items():
            if v["n_up"] or v["n_down"]:
                print(
                    f"  {f:20s} {v['strict_mean']:.3f} -> {v['corrected_mean']:.3f} "
                    f"(delta {v['delta']:+.3f} [{v['delta_ci95'][0]:+.3f},{v['delta_ci95'][1]:+.3f}]"
                    f") up={v['n_up']} down={v['n_down']}"
                )
    print(f"Written: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
