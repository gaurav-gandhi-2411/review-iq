"""S17 W4b: same-prompt noise floor for the ADR 0031 `buy_again` experiment.

The ADR 0031 variant moved `sentiment` on 8 of 56 fixtures and `buy_again` coverage 2 -> 4, and no
same-prompt repeat was ever run, so there was no way to say how much of that is run-to-run
variation of the UNCHANGED production prompt. This module is that control: it re-runs the
unmodified production English prompt (`app.core.prompts.en.build_prompt`, the exact builder
production uses) on a deterministic subset of the 56 English-routed held-out fixtures and compares
the repeat against the recorded baseline (`held_out_scoring_v2.json`) -- and, on the same
fixtures, against the recorded variant -- so the variant's effect can be judged against the floor.

Why a subset: the 56 fixtures cost ~2.5K tokens each on the small model (~142K), over the 100K
per-model-per-day ceiling. The subset is chosen to cover where change can happen, not at random:
tier 1 = fixtures where either arm committed `buy_again` or the variant changed `sentiment`;
tier 2 = every baseline-`mixed` fixture (ADR 0031's own pre-registered control); tier 3 = gold-
decidable fixtures (the only ones where a coverage gain is possible), seed-42 shuffled, until the
subset has N_TARGET members. The 22-ish left out are non-decidable, non-mixed, never-committed:
the fixtures least able to move. A noise floor measured on boundary-enriched fixtures is an UPPER
bound on the floor over all 56, which is the conservative direction for judging the variant.

Modes: record (live Groq, cassette-recorded, budget-guarded) | replay (zero quota) | report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.prompts import en as en_prompt  # noqa: E402

from eval.provenance import get_git_sha, now_iso  # noqa: E402

HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
BASELINE_PATH = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
VARIANT_DAY_PATHS = tuple(ROOT / "eval" / "results" / f"buy_again_exp_day{d}.json" for d in (1, 2))
CASSETTE_PATH = ROOT / "eval" / "cassettes" / "noise_floor_cassettes.json"
RESULT_PATH = ROOT / "eval" / "results" / "noise_floor_baseline.json"

SEED = 42
# 34 x ~2.54K = ~86K on the small model: under the 95K guard with room for escalations, and
# leaves production's half of the 200K TPD pool untouched (measured 0 production tokens at start).
N_TARGET = 34
TOKEN_CEILING_PER_MODEL = 95_000
EST_TOKENS_PER_CALL = 2_900  # measured mean t_in 2123 - ~373 (extra examples) + t_out 790, +margin
PACING_SECONDS = 28.0  # keeps ~2.5K-token calls under the 8K TPM per-model limit


def english_routed_ids(records: list[dict[str, Any]]) -> list[str]:
    return sorted(r["id"] for r in records if r["detected_language"] == "en")


def load_variant_rows() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for p in VARIANT_DAY_PATHS:
        for r in json.loads(p.read_text(encoding="utf-8"))["rows"]:
            rows[r["id"]] = r
    return rows


def select_ids(variant_rows: dict[str, dict[str, Any]], n_target: int = N_TARGET) -> list[str]:
    """Deterministic tiered subset (see module docstring). Sorted ids out."""
    tier1 = {
        i
        for i, r in variant_rows.items()
        if r["baseline_pred"] is not None
        or r["variant_pred"] is not None
        or r["baseline_sentiment"] != r["variant_sentiment"]
    }
    tier2 = {i for i, r in variant_rows.items() if r["baseline_sentiment"] == "mixed"} - tier1
    chosen = set(tier1) | tier2
    rest = sorted(i for i, r in variant_rows.items() if r["gold"] is not None and i not in chosen)
    random.Random(SEED).shuffle(rest)
    for i in rest:
        if len(chosen) >= n_target:
            break
        chosen.add(i)
    if len(chosen) > n_target:  # tiers 1+2 alone exceed the budget: refuse rather than truncate
        raise ValueError(f"tiers 1+2 need {len(chosen)} fixtures > N_TARGET={n_target}")
    return sorted(chosen)


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]] (sum of tables no more likely than observed)."""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2

    def pmf(x: int) -> float:
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(n, k)

    lo, hi = max(0, k - n2), min(k, n1)
    p_obs = pmf(a)
    return min(1.0, sum(pmf(x) for x in range(lo, hi + 1) if pmf(x) <= p_obs * (1 + 1e-9)))


def load_gold() -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted(HELD_OUT_DIR.glob("hien-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["id"]] = d
    return out


async def run(mode: str) -> None:
    import app.core.providers.cassette as cassette_module
    from app.core.config import get_settings
    from app.core.language import detect_language
    from app.core.llm import _SYSTEM_PROMPT
    from app.core.router import route_extraction
    from app.core.sanitize import sanitize, wrap_for_llm

    os.environ["EVAL_CASSETTE_MODE"] = mode
    cassette_module.CASSETTES_PATH = CASSETTE_PATH
    settings = get_settings()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    records = {r["id"]: r for r in baseline["records"]}
    variant_rows = load_variant_rows()
    gold = load_gold()
    ids = select_ids(variant_rows)
    assert set(ids) <= set(english_routed_ids(baseline["records"]))

    if mode == "record":
        # S19 Q3a: in-run `used` is blind to other consumers of the key; read Groq's live counters.
        from eval.quota_guard import preflight

        preflight(
            {
                settings.groq_model_small: len(ids) * EST_TOKENS_PER_CALL,
                settings.groq_model_large: 0,
            },
            api_key=settings.groq_api_key,
        )

    rows: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    for n, fid in enumerate(ids, 1):
        clean, _ = sanitize(gold[fid]["review_text"])
        assert detect_language(clean) == "en", f"{fid} is not English-routed under this detector"
        if mode == "record":
            worst = max(used.values(), default=0)
            if worst + EST_TOKENS_PER_CALL > TOKEN_CEILING_PER_MODEL:
                print(f"BUDGET GUARD: stopping before {fid}: used={used}")
                break
        prompt = en_prompt.build_prompt(wrap_for_llm(clean))  # the production prompt, unmodified
        out, model, t_in, t_out, escalated, degraded = await route_extraction(
            prompt, _SYSTEM_PROMPT, settings=settings
        )
        final = out.model_dump()
        used[model] = used.get(model, 0) + t_in + t_out
        if escalated:  # the small-tier attempt also spent tokens; estimate it as the same size
            used[settings.groq_model_small] = used.get(settings.groq_model_small, 0) + t_in + t_out
        base = records[fid]["as_deployed"]
        g = gold[fid]["ground_truth"]["buy_again"]
        v = variant_rows[fid]
        rows.append(
            {
                "id": fid,
                "gold": g,
                "baseline_pred": base["predicted"]["buy_again"],
                "repeat_pred": final["buy_again"],
                "variant_pred": v["variant_pred"],
                "baseline_score": base["field_scores"]["buy_again"],
                "repeat_score": 1.0 if final["buy_again"] == g else 0.0,
                "variant_score": v["variant_score"],
                "baseline_sentiment": base["predicted"]["sentiment"],
                "repeat_sentiment": final["sentiment"],
                "variant_sentiment": v["variant_sentiment"],
                "model": model,
                "escalated": escalated,
                "degraded": degraded,
                "tokens_in": t_in,
                "tokens_out": t_out,
            }
        )
        print(
            f"  [{n}/{len(ids)}] {fid}: gold={g} base={rows[-1]['baseline_pred']} "
            f"repeat={final['buy_again']} sent {rows[-1]['baseline_sentiment']}->{final['sentiment']}"
        )
        if mode == "record":
            await asyncio.sleep(PACING_SECONDS)

    RESULT_PATH.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "git_sha": get_git_sha(),
                "adr": "docs/architecture/adr/0031-buy-again-english-fewshot-experiment.md",
                "mode": mode,
                "seed": SEED,
                "n_target": N_TARGET,
                "n_planned": len(ids),
                "n_run": len(rows),
                "baseline_artifact": "eval/results/held_out_scoring_v2.json",
                "baseline_git_sha": baseline["git_sha"],
                "tokens_by_model_estimated": used,
                "token_ceiling_per_model": TOKEN_CEILING_PER_MODEL,
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Written {RESULT_PATH}; tokens (est.): {used}")


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts the report prints; pure so it is unit-testable."""
    n = len(rows)

    def cov(key: str) -> int:
        return sum(1 for r in rows if r[key] is not None)

    def wrong(pred: str, score: str) -> int:
        return sum(1 for r in rows if r[pred] is not None and r[score] == 0.0)

    def sent_changed(arm: str) -> int:
        return sum(1 for r in rows if r["baseline_sentiment"] != r[arm])

    def commit_changed(arm: str) -> int:
        return sum(1 for r in rows if (r["baseline_pred"] is None) != (r[arm] is None))

    mixed = [r for r in rows if r["baseline_sentiment"] == "mixed"]
    return {
        "n": n,
        "coverage": {
            "baseline": cov("baseline_pred"),
            "repeat": cov("repeat_pred"),
            "variant": cov("variant_pred"),
        },
        "wrong_committed": {
            "baseline": wrong("baseline_pred", "baseline_score"),
            "repeat": wrong("repeat_pred", "repeat_score"),
            "variant": wrong("variant_pred", "variant_score"),
        },
        "commit_changed_vs_baseline": {
            "repeat": commit_changed("repeat_pred"),
            "variant": commit_changed("variant_pred"),
        },
        "sentiment_changed_vs_baseline": {
            "repeat": sent_changed("repeat_sentiment"),
            "variant": sent_changed("variant_sentiment"),
        },
        "baseline_mixed": {
            "n": len(mixed),
            "left_mixed_repeat": sum(1 for r in mixed if r["repeat_sentiment"] != "mixed"),
            "left_mixed_variant": sum(1 for r in mixed if r["variant_sentiment"] != "mixed"),
        },
    }


def report() -> None:
    j = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    rows = j["rows"]
    s = summarise(rows)
    n = s["n"]
    print(
        f"noise-floor arm: {n} fixtures run of {j['n_planned']} planned (mode={j['mode']}, "
        f"git {j['git_sha'][:7]}, baseline recorded at {j['baseline_git_sha'][:7]})"
    )
    print("coverage (committed buy_again): ", s["coverage"])
    print("wrong-committed count:          ", s["wrong_committed"])
    cc, sc = s["commit_changed_vs_baseline"], s["sentiment_changed_vs_baseline"]
    print("fixtures whose commit/null state differs from recorded baseline:", cc)
    print("fixtures whose sentiment differs from recorded baseline:        ", sc)
    print("baseline-mixed fixtures:", s["baseline_mixed"])
    for label, key in (("commit state", cc), ("sentiment", sc)):
        p = fisher_exact_two_sided(
            key["variant"], n - key["variant"], key["repeat"], n - key["repeat"]
        )
        print(
            f"variant vs same-prompt repeat, {label} changed: {key['variant']}/{n} vs "
            f"{key['repeat']}/{n}, Fisher exact two-sided p={p:.3f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["record", "replay", "report"])
    args = ap.parse_args()
    if args.mode == "report":
        report()
    else:
        asyncio.run(run(args.mode))


if __name__ == "__main__":
    main()
