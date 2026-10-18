"""Amendment 13: is `reasoning_effort` honoured by the pinned Ollama build? Reads the kernel's status.json ("effort" block).

    python -m engine.intent.llm_effort_check --status path/to/status.json --out reports/intent/moat_effort_check/effort_check.json

Criterion (registered before any row existed): HONOURED iff the paired two-sided exact sign test on completion tokens, high versus low
over the same items (ties dropped), has p < 0.05 AND median(high) >= 1.25 x median(low). Counts only; no review text is read.
"""

from __future__ import annotations

import argparse
import json
import statistics
from math import comb
from pathlib import Path

P_MAX = 0.05
RATIO_MIN = 1.25


def sign_test_p(pos: int, neg: int) -> float:
    """Two-sided exact binomial (p=0.5) sign test on the non-tied pairs."""
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)


def summarise(ct: dict[str, int]) -> dict:
    v = list(ct.values())
    return {
        "n": len(v),
        "mean": round(statistics.fmean(v), 1) if v else None,
        "median": statistics.median(v) if v else None,
        "max": max(v) if v else None,
    }


def evaluate(effort_block: list[dict]) -> dict:
    by = {e["effort"]: e for e in effort_block}
    out: dict = {
        "per_setting": {
            k: {
                **summarise(e["completion_tokens"]),
                "empty": e["empty"],
                "finish_length": e["finish_length"],
            }
            for k, e in by.items()
        },
        "criterion": {"p_max": P_MAX, "median_ratio_min": RATIO_MIN},
    }
    lo, hi = by["low"]["completion_tokens"], by["high"]["completion_tokens"]
    ids = sorted(set(lo) & set(hi))
    diffs = [hi[i] - lo[i] for i in ids]
    pos, neg = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    p = sign_test_p(pos, neg)
    med_lo = statistics.median(lo[i] for i in ids) if ids else 0
    med_hi = statistics.median(hi[i] for i in ids) if ids else 0
    ratio = med_hi / med_lo if med_lo else None
    out["high_vs_low"] = {
        "paired_items": len(ids),
        "high_greater": pos,
        "low_greater": neg,
        "ties": len(ids) - pos - neg,
        "sign_test_p": round(p, 6),
        "median_low": med_lo,
        "median_high": med_hi,
        "median_ratio": round(ratio, 3) if ratio else None,
    }
    out["honoured"] = bool(p < P_MAX and ratio is not None and ratio >= RATIO_MIN)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = evaluate(json.loads(Path(a.status).read_text(encoding="utf-8"))["effort"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
