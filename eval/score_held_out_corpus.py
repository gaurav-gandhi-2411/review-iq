"""P3 (Session 10): score current production models against the quarantined held-out corpus.

This is the FIRST uncontaminated measurement this product has ever had -- every other number
in eval/results.json comes from fixtures the prompt was developed and iterated against (see
eval/README.md's D3 contamination discussion). The 23 fixtures under
eval/fixtures/_held_out_hindi_hinglish/ were built (Session 9) and never seen by anyone
developing app/core/prompts/**, structurally excluded from eval/runner.py's own fixture walk.

Hits the LIVE deployed demo endpoint (POST /demo/extract) rather than calling extract_with_llm
in-process: demo.py and app/api/v2/extract.py both call the identical extract_with_llm() (same
tiered router, same prompts), so the demo endpoint is a faithful, keyless measurement of exactly
what a real customer's /v2/extract call would produce -- this measures the ACTUALLY DEPLOYED
code+config, not this checkout's local state.

Ground truth here is LLM-consensus SILVER (Session 9 P3f), never human-verified -- report
accordingly, never as ground truth in the human-labeled sense.

Usage:
    uv run python eval/score_held_out_corpus.py
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from eval.bootstrap import DEFAULT_N_RESAMPLES, DEFAULT_SEED, bootstrap_ci  # noqa: E402
from eval.runner import score_fixture  # noqa: E402

QUARANTINE_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
DEMO_URL = "https://review-iq-vd6nfefddq-el.a.run.app/demo/extract"
OUT_PATH = ROOT / "eval" / "results" / "held_out_scoring_latest.json"

DELAY_SECONDS = 15.0  # comfortably under the demo endpoint's real 5/minute limit (12s/call floor)

# Fields that may legitimately hedge (null/mixed) -- coverage and accuracy-on-answered are
# computed over these, matching this project's existing hedge-analysis vocabulary
# (docs/specs/wave1-coverage-abstention-analysis.md).
HEDGE_FIELDS: dict[str, Any] = {"sentiment": "mixed", "buy_again": None}


def load_quarantined_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for p in sorted(QUARANTINE_DIR.glob("*.json")):
        if p.name.startswith("."):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if "review_text" in data:
            fixtures.append(data)
    return fixtures


async def score_one(client: httpx.AsyncClient, fixture: dict[str, Any]) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        resp = await client.post(DEMO_URL, json={"text": fixture["review_text"]}, timeout=30.0)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            return {
                "id": fixture["id"],
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "overall_score": 0.0,
                "latency_ms": latency_ms,
            }
        extraction = resp.json()
        field_results = score_fixture(fixture, extraction)
        scores = [fr.score for fr in field_results]
        overall = sum(scores) / len(scores) if scores else 0.0
        return {
            "id": fixture["id"],
            "overall_score": overall,
            "latency_ms": latency_ms,
            "field_scores": {fr.field: fr.score for fr in field_results},
            "predicted": {fr.field: fr.predicted for fr in field_results},
            "expected": {fr.field: fr.expected for fr in field_results},
        }
    except Exception as exc:  # noqa: BLE001 -- record, don't crash the whole run
        return {
            "id": fixture["id"],
            "error": str(exc),
            "overall_score": 0.0,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }


def two_sample_bootstrap_diff(
    a: list[float], b: list[float], n_resamples: int = DEFAULT_N_RESAMPLES, seed: int = DEFAULT_SEED
) -> tuple[float, float]:
    """95% percentile-bootstrap CI for mean(a) - mean(b), independent samples."""
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    diffs = []
    for _ in range(n_resamples):
        ra = mean(a[rng.randrange(na)] for _ in range(na))
        rb = mean(b[rng.randrange(nb)] for _ in range(nb))
        diffs.append(ra - rb)
    diffs.sort()
    lo = diffs[int(0.025 * n_resamples)]
    hi = diffs[min(int(0.975 * n_resamples), n_resamples - 1)]
    return (lo, hi)


async def main() -> None:
    fixtures = load_quarantined_fixtures()

    existing_by_id: dict[str, dict[str, Any]] = {}
    if OUT_PATH.exists():
        prior = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        existing_by_id = {r["id"]: r for r in prior.get("per_fixture", [])}

    pending = [fx for fx in fixtures if "error" in existing_by_id.get(fx["id"], {"error": True})]
    print(
        f"Scoring {len(fixtures)} quarantined fixtures against the live demo endpoint "
        f"({len(fixtures) - len(pending)} already scored, {len(pending)} pending/retrying)..."
    )

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for i, fx in enumerate(pending, 1):
            r = await score_one(client, fx)
            results.append(r)
            print(
                f"  [{i}/{len(pending)}] {r['id']}: score={r['overall_score']:.2f} "
                f"{r.get('error', '')}"
            )
            if i < len(pending):
                await asyncio.sleep(DELAY_SECONDS)

    for fx in fixtures:
        if fx["id"] not in {r["id"] for r in results} and fx["id"] in existing_by_id:
            results.append(existing_by_id[fx["id"]])
    results.sort(key=lambda r: next(i for i, fx in enumerate(fixtures) if fx["id"] == r["id"]))

    n_errors = sum(1 for r in results if "error" in r)
    scored = [r for r in results if "error" not in r]
    overall_scores = [r["overall_score"] for r in scored]
    overall = mean(overall_scores) if overall_scores else 0.0
    overall_ci = bootstrap_ci(overall_scores) if overall_scores else (0.0, 0.0)

    # Per-field accuracy, coverage, accuracy-on-answered for the hedge-capable fields.
    per_field: dict[str, Any] = {}
    all_fields = {f for r in scored for f in r.get("field_scores", {})}
    for field in sorted(all_fields):
        field_scores = [r["field_scores"][field] for r in scored if field in r["field_scores"]]
        field_acc = mean(field_scores) if field_scores else None
        entry: dict[str, Any] = {"n": len(field_scores), "accuracy": field_acc}
        if field in HEDGE_FIELDS:
            hedge_value = HEDGE_FIELDS[field]
            answered = [
                r
                for r in scored
                if field in r.get("predicted", {}) and r["predicted"][field] != hedge_value
            ]
            n_total = sum(1 for r in scored if field in r.get("predicted", {}))
            entry["coverage"] = (len(answered) / n_total) if n_total else None
            if answered:
                answered_scores = [r["field_scores"][field] for r in answered]
                entry["accuracy_on_answered"] = mean(answered_scores)
                entry["accuracy_on_answered_ci_95"] = bootstrap_ci(answered_scores)
            else:
                entry["accuracy_on_answered"] = None
        per_field[field] = entry

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus": "eval/fixtures/_held_out_hindi_hinglish/ (quarantined, LLM-consensus silver, never human ground truth)",
        "measurement_target": "live deployed demo endpoint (POST /demo/extract), which calls the identical extract_with_llm() as /v2/extract",
        "n_fixtures": len(fixtures),
        "n_scored": len(scored),
        "n_errors": n_errors,
        "overall_score": overall,
        "overall_ci_95": {
            "lower": overall_ci[0],
            "upper": overall_ci[1],
            "method": "bootstrap",
            "n": len(overall_scores),
        },
        "per_field": per_field,
        "per_fixture": results,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 60)
    print(
        f"Overall (uncontaminated, n={len(scored)}): {overall:.1%} "
        f"[{overall_ci[0]:.1%}, {overall_ci[1]:.1%}]"
    )
    print(f"Errors: {n_errors}")
    print(f"Written: {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
