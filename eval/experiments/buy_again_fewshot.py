"""S6 (Session 15c): the pre-registered `buy_again` English few-shot experiment (ADR 0031).

Builds a variant of the production English prompt with two extra `buy_again` few-shot examples
(one `true`, one `false`) and measures coverage and wrong-committed on the 56 English-routed
held-out fixtures against the recorded baseline. `app/core/prompts/en.py` is NOT modified.

Modes:
    record --day 1|2   live Groq calls (cassette-recorded), budget-guarded, writes the day's JSON
    replay --day 1|2   re-derive the day's JSON from the cassette (zero quota)
    report             combine whichever days exist and print the pre-registered verdict

Day split (fixed in ADR 0031): the 56 English-routed ids sorted; day 1 = even positions,
day 2 = odd positions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.prompts import en as en_prompt  # noqa: E402

from eval.provenance import get_git_sha, now_iso  # noqa: E402

HELD_OUT_DIR = ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
BASELINE_PATH = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
CASSETTE_PATH = ROOT / "eval" / "cassettes" / "buy_again_exp_cassettes.json"
RESULTS_DIR = ROOT / "eval" / "results"

# Ceiling per model per day: 50% of the 200K TPD pool, minus a 5% safety margin (ADR 0031).
TOKEN_CEILING_PER_MODEL = 95_000
EST_TOKENS_PER_CALL = 3_200  # ~2.3K prompt with the added examples + ~0.9K output
PACING_SECONDS = 28.0  # keeps ~3K-token calls under the 8K TPM per-model limit

B_COV = 2  # baseline committed count on the 56 English-routed fixtures (ADR 0031)
B_WRONG = 1  # baseline wrong-committed count (hien-0038)

# Synthetic examples, written for this experiment. Not from any corpus; a unit test asserts no
# sentence of these occurs in any held-out or dev fixture.
EXTRA_EXAMPLES = """
Example — specific praise and an explicit recommendation (buy_again=true):
Review: "Bought this electric kettle two months ago. It boils a full litre in under three minutes, the auto shut-off has never failed, and the handle stays cool. Best kitchen purchase I've made this year and I'd recommend it to anyone."
Output: {"product": "electric kettle", "stars": null, "stars_inferred": 5, "pros": ["fast boiling", "reliable auto shut-off", "cool handle"], "cons": [], "buy_again": true, "sentiment": "positive", "topics": ["performance", "safety", "design"], "competitor_mentions": [], "urgency": "low", "feature_requests": [], "language": "en", "confidence": 0.9}

Example — specific defect and an explicit statement of not buying again (buy_again=false):
Review: "The backpack's zip split within three weeks and the strap stitching is already coming apart. Support asked me to pay for return shipping. I won't be buying from this brand again."
Output: {"product": "backpack", "stars": null, "stars_inferred": 1, "pros": [], "cons": ["zip split within three weeks", "strap stitching coming apart", "return shipping charged"], "buy_again": false, "sentiment": "negative", "topics": ["build_quality", "durability", "customer_service"], "competitor_mentions": [], "urgency": "medium", "feature_requests": [], "language": "en", "confidence": 0.9}
"""


def build_variant_prompt(wrapped_review: str) -> str:
    """The production English prompt with the extra examples appended to its example block."""
    return en_prompt._TEMPLATE.format(
        field_descriptions=en_prompt._FIELD_DESCRIPTIONS,
        examples=en_prompt._EXAMPLES.rstrip("\n") + "\n" + EXTRA_EXAMPLES,
        wrapped_review=wrapped_review,
    )


def load_gold() -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted(HELD_OUT_DIR.glob("hien-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["id"]] = d
    return out


def english_routed_ids(records: list[dict[str, Any]]) -> list[str]:
    return sorted(r["id"] for r in records if r["detected_language"] == "en")


def day_ids(ids: list[str], day: int) -> list[str]:
    """Day 1 = even positions, day 2 = odd positions of the sorted English-routed ids."""
    if day not in (1, 2):
        raise ValueError("day must be 1 or 2")
    return [i for pos, i in enumerate(ids) if pos % 2 == (0 if day == 1 else 1)]


def verdict(n_cov: int, n_wrong: int, *, complete: bool) -> str:
    """The pre-registered rule (ADR 0031). REVERT on any rise in the wrong-committed COUNT."""
    if n_wrong > B_WRONG:
        return "REVERT: wrong-committed count rose above baseline"
    if not complete:
        return "INTERIM: within the wrong-committed bound so far; coverage judged after both days"
    if n_cov > B_COV:
        return "SUCCESS: coverage rose and wrong-committed did not"
    return "NULL: coverage did not rise"


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p from discordant counts (b: base commit only, c: new commit only)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * p)


async def run_day(day: int, mode: str) -> None:
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
    gold = load_gold()
    ids = day_ids(english_routed_ids(baseline["records"]), day)
    out_path = RESULTS_DIR / f"buy_again_exp_day{day}.json"

    rows: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    for n, fid in enumerate(ids, 1):
        text = gold[fid]["review_text"]
        clean, _ = sanitize(text)
        lang = detect_language(clean)
        assert lang == "en", f"{fid} detected {lang}: not English-routed under current detector"
        if mode == "record":
            small = used.get(settings.groq_model_small, 0)
            large = used.get(settings.groq_model_large, 0)
            if max(small, large) + EST_TOKENS_PER_CALL > TOKEN_CEILING_PER_MODEL:
                print(f"BUDGET GUARD: stopping before {fid}: small={small} large={large}")
                break
        prompt = build_variant_prompt(wrap_for_llm(clean))
        out, model, t_in, t_out, escalated, degraded = await route_extraction(
            prompt, _SYSTEM_PROMPT, allow_gemini_fallback=False, settings=settings
        )
        final = out.model_dump()
        used[model] = used.get(model, 0) + t_in + t_out
        if escalated:  # the small-tier attempt also spent tokens; estimate it as the same size
            small_model = settings.groq_model_small
            used[small_model] = used.get(small_model, 0) + t_in + t_out
        pred = final["buy_again"]
        base_pred = records[fid]["as_deployed"]["predicted"]["buy_again"]
        g = gold[fid]["ground_truth"]["buy_again"]
        rows.append(
            {
                "id": fid,
                "gold": g,
                "baseline_pred": base_pred,
                "variant_pred": pred,
                "variant_score": 1.0 if pred == g else 0.0,
                "baseline_score": records[fid]["as_deployed"]["field_scores"]["buy_again"],
                "baseline_sentiment": records[fid]["as_deployed"]["predicted"]["sentiment"],
                "variant_sentiment": final["sentiment"],
                "model": model,
                "escalated": escalated,
                "degraded": degraded,
                "tokens_in": t_in,
                "tokens_out": t_out,
            }
        )
        print(f"  [{n}/{len(ids)}] {fid}: gold={g} base={base_pred} variant={pred} model={model}")
        if mode == "record":
            await asyncio.sleep(PACING_SECONDS)

    result = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "adr": "docs/architecture/adr/0031-buy-again-english-fewshot-experiment.md",
        "day": day,
        "mode": mode,
        "n_planned": len(ids),
        "n_run": len(rows),
        "tokens_by_model_estimated": used,
        "token_ceiling_per_model": TOKEN_CEILING_PER_MODEL,
        "rows": rows,
    }
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Written {out_path}; tokens (est.): {used}")


def report() -> None:
    rows: list[dict[str, Any]] = []
    days = []
    for d in (1, 2):
        p = RESULTS_DIR / f"buy_again_exp_day{d}.json"
        if p.exists():
            j = json.loads(p.read_text(encoding="utf-8"))
            days.append(d)
            rows += j["rows"]
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    all_ids = english_routed_ids(baseline["records"])
    complete = len(rows) == len(all_ids)
    n = len(rows)
    b_cov = sum(1 for r in rows if r["baseline_pred"] is not None)
    n_cov = sum(1 for r in rows if r["variant_pred"] is not None)
    b_wrong = sum(1 for r in rows if r["baseline_pred"] is not None and r["baseline_score"] == 0.0)
    n_wrong = sum(1 for r in rows if r["variant_pred"] is not None and r["variant_score"] == 0.0)
    dec = [r for r in rows if r["gold"] is not None]
    only_base = sum(1 for r in rows if r["baseline_pred"] is not None and r["variant_pred"] is None)
    only_new = sum(1 for r in rows if r["baseline_pred"] is None and r["variant_pred"] is not None)
    sent_same = sum(1 for r in rows if r["baseline_sentiment"] == r["variant_sentiment"])
    print(f"days run: {days}; fixtures: {n}/{len(all_ids)} (complete={complete})")
    print(f"baseline on the same fixtures: coverage {b_cov}/{n}, wrong-committed {b_wrong}")
    print(f"variant: coverage {n_cov}/{n}, wrong-committed {n_wrong}")
    print(
        f"commit changes: only-baseline {only_base}, only-variant {only_new}, exact McNemar p={mcnemar_exact_p(only_base, only_new):.4f}"
    )
    print(
        f"gold-decidable fixtures: {len(dec)}; variant committed on {sum(1 for r in dec if r['variant_pred'] is not None)}, correct on {sum(1 for r in dec if r['variant_pred'] == r['gold'])}"
    )
    print(f"sentiment unchanged: {sent_same}/{n}")
    print(
        "wrong-committed ids:",
        [
            (r["id"], r["gold"], r["variant_pred"])
            for r in rows
            if r["variant_pred"] is not None and r["variant_score"] == 0.0
        ],
    )
    print("VERDICT:", verdict(n_cov, n_wrong, complete=complete))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["record", "replay", "report"])
    ap.add_argument("--day", type=int, choices=[1, 2])
    args = ap.parse_args()
    if args.mode == "report":
        report()
    else:
        if args.day is None:
            ap.error("--day is required for record/replay")
        asyncio.run(run_day(args.day, args.mode))


if __name__ == "__main__":
    main()
