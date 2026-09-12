"""End-to-end corpus-mining pipeline orchestrator — SMALL, DOCUMENTED sample only.

Wires together every stage this section adds, in order:
  1. near-dup filter   (near_dup_filter.py)    — HF Inference API, free, no Groq quota
  2. PII scrub          (pii_scrub.py)          — local, no network at all
  3. language strata     (language_strata.py)    — local, no network at all
  4. teacher labeling    (teacher_labeling.py)   — Groq, dedicated benchmark key
  5. consensus validate  (consensus_validate.py) — Groq, dedicated benchmark key
  6. adversarial pairs   (adversarial_pairs.py)  — Groq, dedicated benchmark key

This script is a SAMPLE-SIZE-BOUNDED demonstration of the pipeline working end to
end, not a full-corpus run — see the module docstrings of each stage and
`docs/architecture/adr/0004-corpus-mining-pipeline-and-target-volume.md` for why a
full run is explicitly out of scope here (Groq quota-consumption incident, Wave 1
Section B, 2026-07-07/2026-07-31 — a large unbounded run against a shared free-tier
Groq key is the exact standing escalation trigger this script is built to never hit
by construction: it refuses to make any live call above `HARD_CALL_CAP` without
`--i-understand-the-cost`).

Cost estimate is printed BEFORE any live call, computed from Groq's published
per-token pricing (groq.com/pricing, checked 2026-07-31 — this repo has no cost-
telemetry constant yet, that's Wave 1 Section G, "in progress" as of this writing;
recompute from Section G's real pricing table once it lands instead of this
hand-checked snapshot).

Usage (dry run — no network, prints the plan + cost estimate only):
    uv run python -m benchmark.vernacular_v2.corpus_pipeline.run_corpus_pipeline \\
        --input data/processed/flipkart_classified.jsonl --n 20

Usage (actually runs the small live sample — requires the dedicated benchmark key,
see benchmark_groq_key.py):
    uv run python -m benchmark.vernacular_v2.corpus_pipeline.run_corpus_pipeline \\
        --input data/processed/flipkart_classified.jsonl --n 20 --i-understand-the-cost
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Approximate published per-million-token USD rates (Groq pricing page, checked
# 2026-07-31) — NOT a repo-tracked constant (Section G hasn't shipped one yet).
_GROQ_PRICE_PER_M_TOKENS_USD: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),  # (input, output)
    "openai/gpt-oss-120b": (0.15, 0.60),
    "qwen/qwen3.6-27b": (0.60, 3.00),
}

# Rough per-call token budget assumptions for this pipeline's prompts (system + user
# prompt + JSON schema instructions in, structured extraction or short generation
# out) — deliberately generous/conservative (overestimates cost) rather than tuned
# to look small.
_ASSUMED_TOKENS_IN_EXTRACTION = 700
_ASSUMED_TOKENS_OUT_EXTRACTION = 300
_ASSUMED_TOKENS_IN_GENERATION = 450
_ASSUMED_TOKENS_OUT_GENERATION = 200

HARD_CALL_CAP = 200  # refuse to plan a run above this many total live Groq calls
# without an explicit --i-understand-the-cost confirmation — see module docstring.


def estimate_cost(model_id: str, n_calls: int, *, generation: bool = False) -> float:
    """USD estimate for `n_calls` calls to `model_id` under the assumed token budget."""
    price_in, price_out = _GROQ_PRICE_PER_M_TOKENS_USD[model_id]
    tokens_in = _ASSUMED_TOKENS_IN_GENERATION if generation else _ASSUMED_TOKENS_IN_EXTRACTION
    tokens_out = _ASSUMED_TOKENS_OUT_GENERATION if generation else _ASSUMED_TOKENS_OUT_EXTRACTION
    return n_calls * (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def build_plan(n: int, *, n_adversarial_per_type_per_model: int = 2) -> dict:
    """Return the call-count/cost plan for a run at sample size `n` — pure function,
    no network, safe to call from tests and from the dry-run path."""
    teacher_calls = n
    consensus_calls_per_judge = n
    n_generator_models = 2  # GENERATOR_MODELS / JUDGE_MODELS both have 2 entries
    adversarial_calls_per_model = 3 * n_adversarial_per_type_per_model  # 3 attack types

    cost_teacher = estimate_cost("llama-3.3-70b-versatile", teacher_calls)
    cost_consensus = estimate_cost(
        "openai/gpt-oss-120b", consensus_calls_per_judge
    ) + estimate_cost("qwen/qwen3.6-27b", consensus_calls_per_judge)
    cost_adversarial = estimate_cost(
        "openai/gpt-oss-120b", adversarial_calls_per_model, generation=True
    ) + estimate_cost("qwen/qwen3.6-27b", adversarial_calls_per_model, generation=True)

    total_calls = (
        teacher_calls
        + consensus_calls_per_judge * n_generator_models
        + adversarial_calls_per_model * n_generator_models
    )
    return {
        "sample_size_n": n,
        "teacher_labeling_calls": teacher_calls,
        "consensus_validation_calls": consensus_calls_per_judge * n_generator_models,
        "adversarial_generation_calls": adversarial_calls_per_model * n_generator_models,
        "total_live_groq_calls": total_calls,
        "estimated_cost_usd": {
            "teacher_labeling": round(cost_teacher, 4),
            "consensus_validation": round(cost_consensus, 4),
            "adversarial_generation": round(cost_adversarial, 4),
            "total": round(cost_teacher + cost_consensus + cost_adversarial, 4),
        },
        "within_hard_call_cap": total_calls <= HARD_CALL_CAP,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--n", type=int, default=20, help="Small, bounded sample size.")
    parser.add_argument(
        "--n-adversarial-per-type-per-model",
        type=int,
        default=2,
        help="Adversarial samples per (attack_type, generator_model) pair.",
    )
    parser.add_argument(
        "--i-understand-the-cost",
        action="store_true",
        help="Required to actually make live Groq calls. Without it, prints the plan and exits.",
    )
    args = parser.parse_args()

    plan = build_plan(
        args.n, n_adversarial_per_type_per_model=args.n_adversarial_per_type_per_model
    )
    print("=" * 60)
    print("CORPUS-MINING PIPELINE — RUN PLAN (small, documented sample)")
    print("=" * 60)
    for k, v in plan.items():
        print(f"  {k}: {v}")

    if not plan["within_hard_call_cap"]:
        print(
            f"\nREFUSING: {plan['total_live_groq_calls']} calls exceeds HARD_CALL_CAP="
            f"{HARD_CALL_CAP}. Reduce --n or --n-adversarial-per-type-per-model.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.i_understand_the_cost:
        print(
            "\nDRY RUN ONLY — no live calls made. Pass --i-understand-the-cost to actually "
            "run this against the dedicated benchmark Groq key."
        )
        return

    print("\n--- Running live (dedicated benchmark key) ---")
    # Imports deferred to here so the dry-run path above never requires a benchmark
    # key or triggers any Settings/env resolution.
    import subprocess  # noqa: PLC0415, S404 — invoking sibling scripts as subprocesses keeps
    # each stage's own __main__ argument handling / env-injection-before-import
    # ordering intact, rather than re-implementing it inline here.

    stages = [
        [
            sys.executable,
            "-m",
            "benchmark.vernacular_v2.corpus_pipeline.teacher_labeling",
            "--input",
            str(args.input),
            "--output",
            str(
                ROOT
                / "benchmark"
                / "vernacular_v2"
                / "corpus_pipeline"
                / "teacher_labels_sample.jsonl"
            ),
            "--n",
            str(args.n),
        ],
        [
            sys.executable,
            "-m",
            "benchmark.vernacular_v2.corpus_pipeline.consensus_validate",
            "--input",
            str(args.input),
            "--output",
            str(
                ROOT / "benchmark" / "vernacular_v2" / "corpus_pipeline" / "consensus_sample.jsonl"
            ),
            "--n",
            str(args.n),
        ],
        [
            sys.executable,
            "-m",
            "benchmark.vernacular_v2.corpus_pipeline.adversarial_pairs",
            "--real-reviews-input",
            str(args.input),
            "--n-per-type-per-model",
            str(args.n_adversarial_per_type_per_model),
        ],
    ]
    for stage_cmd in stages:
        print(f"\n$ {' '.join(stage_cmd)}")
        result = subprocess.run(stage_cmd, cwd=ROOT, check=False)  # noqa: S603
        if result.returncode != 0:
            print(f"Stage failed (exit {result.returncode}): {stage_cmd}", file=sys.stderr)
            sys.exit(result.returncode)

    print("\nAll stages complete.")


if __name__ == "__main__":
    main()
