"""S19 N1 "moat test" harness: does a general-purpose model with a PLAIN prompt handle the held-out
Hinglish reviews as well as the production pipeline?

Question this answers (and the one it does not): the README headline is "79.6% on the 70 unseen
held-out reviews, scored where the three-judge panel agreed". A prospect's alternative is not our
prompt on gpt-oss; it is "paste the review into GPT / Claude / Gemini". This script runs the SAME 70
reviews through a chosen OpenRouter model with a plain prompt asking for the SAME fields, and scores
the result with the SAME scorer (`eval.runner.score_fixture` -> `eval.score_held_out_corpus_v2.
_headline_cell` -> `eval.analyze_coverage_metrics.analyze`), so the numbers sit in the same table as
the headline. It does NOT test competitors' products (those have no API we can call without a sales
conversation); see the S19 N1a report.

Privacy (hard rules, enforced in code, not by convention):
  * The review text is third-party text. Only OpenRouter endpoints on the live Zero-Data-Retention
    allowlist may receive it. Every request carries `provider.zdr = true` (the adapter inherits
    `app.core.providers.secondary.SecondaryProvider`, which asserts `trains_on_input = False`), AND
    a pre-flight GET /api/v1/endpoints/zdr must list the model, else the run refuses (fail closed).
  * `--dry-run` makes ZERO network calls and prints ids/char counts/token estimates only -- never
    review text.
  * Live modes need `--confirm-spend` AND `--max-usd`; they stop before a call that could cross the
    cap using the per-call hard ceiling (max_tokens x output price), not the average.

Determinism: seed 42 is sent on every request (providers that ignore `seed` still get
temperature 0); every response is cassette-recorded, so `--mode replay` is $0 and byte-identical.
Cassettes live in eval/cassettes/moat_test_cassettes.json (NOT eval/cassettes/cassettes.json).

Token/cost estimate (dry-run): input tokens = ceil(chars / 3) + 8 per call. char/3 is deliberately
conservative: for Roman-script Hinglish most BPE tokenizers give ~3.5-4.5 chars/token, so this
OVER-estimates input. Output tokens: 400 for a non-reasoning model (a 13-field JSON object), 1,500
for a reasoning-capable model (measured p95 of gpt-oss-20b on these same reviews, eval/results/
token_cost_measurement_n106.json; frontier reasoning models can exceed this unless a reasoning
effort is set, hence the separate hard-ceiling column).

Usage:
    uv run python eval/experiments/moat_test_baseline.py --dry-run
    uv run python eval/experiments/moat_test_baseline.py --dry-run --models openai/gpt-5.5
    # live (NOT run by this PR): needs SECONDARY_PROVIDER_API_KEY (an OpenRouter key)
    uv run python eval/experiments/moat_test_baseline.py --mode record --models openai/gpt-5.5 \
        --confirm-spend --max-usd 1.00
    uv run python eval/experiments/moat_test_baseline.py --mode replay --models openai/gpt-5.5
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.providers.base import assert_privacy_safe  # noqa: E402
from app.core.providers.secondary import SecondaryProvider  # noqa: E402

from eval import analyze_coverage_metrics as coverage  # noqa: E402
from eval.heldout_exposure import (  # noqa: E402
    held_out_exposure,
    load_held_out_fixtures,
    unresolved_fields,
)
from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.runner import score_fixture  # noqa: E402

SEED = 42
CASSETTE_PATH = ROOT / "eval" / "cassettes" / "moat_test_cassettes.json"
RESULTS_DIR = ROOT / "eval" / "results"
PRODUCTION_RECORDS_PATH = RESULTS_DIR / "held_out_scoring_v2.json"

# The published headline's 8 fields (eval/score_held_out_corpus_v2.py::summarize): every scored
# field minus `stars` (null in all gold, a free 1.0) and `language` (echo of the detector / label
# noise, ADR 0023). Pinned here and asserted against the fixtures' own scoring_notes by a test.
HEADLINE_FIELDS: tuple[str, ...] = (
    "product",
    "buy_again",
    "sentiment",
    "topics",
    "competitor_mentions",
    "pros",
    "cons",
    "stars_inferred",
)
EXPECTED_N_UNSEEN = 70  # 106 corpus reviews - 36 the prompt-development process had seen (ADR 0032)

CHARS_PER_TOKEN = 3  # conservative (over-estimates input tokens); see module docstring
MESSAGE_OVERHEAD_TOKENS = 8
OUT_TOKENS_PLAIN = 400
OUT_TOKENS_REASONING = 1_500
MAX_TOKENS_CAP = 4_096  # per-call hard ceiling sent as max_tokens
REQUEST_TIMEOUT_SECONDS = 120
PACING_SECONDS = 0.5


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens, from GET https://openrouter.ai/api/v1/models on PRICING_SNAPSHOT.

    `zdr_endpoints` = number of endpoints for the model on GET /api/v1/endpoints/zdr at the same
    time. 0 means the model CANNOT be used with review text (the run refuses).
    """

    input_per_m: float
    output_per_m: float
    reasoning: bool
    zdr_endpoints: int


PRICING_SNAPSHOT = "2026-10-08"
PRICING: dict[str, ModelPrice] = {
    # --- general-purpose baselines a prospect would compare against ---
    "openai/gpt-5.5": ModelPrice(5.00, 30.00, True, 3),
    "openai/gpt-5": ModelPrice(1.25, 10.00, True, 2),
    "openai/gpt-5.6-sol": ModelPrice(2.00, 10.00, True, 3),
    "openai/gpt-5-mini": ModelPrice(0.25, 2.00, True, 2),
    "anthropic/claude-opus-5.5": ModelPrice(4.00, 20.00, False, 6),
    "anthropic/claude-sonnet-5.5": ModelPrice(2.00, 10.00, False, 4),
    "anthropic/claude-haiku-5.5": ModelPrice(0.10, 0.50, False, 6),
    "google/gemini-3.1-pro-preview": ModelPrice(2.00, 12.00, True, 3),
    "google/gemini-3.5-flash": ModelPrice(1.50, 9.00, True, 4),
    "google/gemini-3.8-flash": ModelPrice(0.75, 3.75, True, 3),
    # --- open-weight / control ---
    "deepseek/deepseek-v4.1-flash": ModelPrice(0.30, 1.20, False, 23),
    "meta-llama/llama-3.3-70b-instruct": ModelPrice(0.22, 0.50, False, 10),
    # production's own large tier with a PLAIN prompt: isolates "our prompt" from "our model".
    "openai/gpt-oss-120b": ModelPrice(0.037, 0.17, True, 22),
}
DEFAULT_MODELS: tuple[str, ...] = tuple(PRICING)

PLAIN_SYSTEM = "You are a product review analyst. Return ONLY valid JSON."
PLAIN_USER_TEMPLATE = """\
Analyse this customer review (Hindi-English / Hinglish, Roman script) and return one JSON object \
with exactly these keys:

product (string), stars (integer 1-5 only if the reviewer states a numeric rating, else null), \
stars_inferred (integer 1-5), pros (list of short English phrases), cons (list of short English \
phrases), buy_again (true, false, or null if unclear), sentiment ("positive", "negative", \
"neutral" or "mixed"), topics (list of short snake_case English topics), competitor_mentions \
(list of brand names), urgency ("low", "medium" or "high"), feature_requests (list of strings), \
language (string), confidence (number 0-1).

Review:
{review}"""


# --------------------------------------------------------------------------- planning (offline)


def unseen_fixtures() -> list[dict[str, Any]]:
    """The 70 held-out reviews the development process had NOT seen, sorted by id."""
    fixtures = load_held_out_fixtures()
    exposed = held_out_exposure()
    return [fixtures[i] for i in sorted(fixtures) if i not in exposed]


@functools.lru_cache(maxsize=512)
def build_messages(review_text: str, style: str = "plain") -> tuple[str, str]:
    """(system_prompt, user_prompt). Text is PII-sanitized exactly as production does."""
    from app.core.sanitize import sanitize, wrap_for_llm

    clean, _ = sanitize(review_text)
    if style == "plain":
        return PLAIN_SYSTEM, PLAIN_USER_TEMPLATE.format(review=clean)
    if style == "production":  # same prompt + system text as the 70/106 headline (hi_en prompt)
        from app.core.llm import _SYSTEM_PROMPT
        from app.core.prompts import hi_en

        return _SYSTEM_PROMPT, hi_en.build_prompt(wrap_for_llm(clean))
    raise ValueError(f"unknown prompt style {style!r}")


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_input_tokens(system: str, user: str) -> int:
    return estimate_tokens(system) + estimate_tokens(user) + MESSAGE_OVERHEAD_TOKENS


def usd(tokens_in: int, tokens_out: int, price: ModelPrice) -> float:
    return (tokens_in * price.input_per_m + tokens_out * price.output_per_m) / 1_000_000


def plan_calls(
    model: str, fixtures: list[dict[str, Any]], style: str = "plain"
) -> list[dict[str, Any]]:
    """One planned call per review: ids and counts only, never text."""
    price = PRICING[model]
    out_expected = OUT_TOKENS_REASONING if price.reasoning else OUT_TOKENS_PLAIN
    calls = []
    for fx in fixtures:
        system, user = build_messages(fx["review_text"], style)
        t_in = estimate_input_tokens(system, user)
        calls.append(
            {
                "id": fx["id"],
                "chars": len(system) + len(user),
                "est_tokens_in": t_in,
                "est_tokens_out": out_expected,
                "est_usd": usd(t_in, out_expected, price),
                "ceiling_usd": usd(t_in, MAX_TOKENS_CAP, price),
            }
        )
    return calls


def summarize_plan(model: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    price = PRICING[model]
    return {
        "model": model,
        "n_calls": len(calls),
        "est_tokens_in": sum(c["est_tokens_in"] for c in calls),
        "est_tokens_out": sum(c["est_tokens_out"] for c in calls),
        "est_usd": sum(c["est_usd"] for c in calls),
        "ceiling_usd": sum(c["ceiling_usd"] for c in calls),
        "reasoning_model": price.reasoning,
        "zdr_endpoints_at_snapshot": price.zdr_endpoints,
    }


def format_plan_table(summaries: list[dict[str, Any]]) -> str:
    lines = [
        f"{'model':36} {'calls':>5} {'tok_in':>8} {'tok_out':>8} {'est_usd':>9} "
        f"{'ceiling':>9} {'zdr_ep':>6}",
    ]
    for s in summaries:
        lines.append(
            f"{s['model']:36} {s['n_calls']:>5} {s['est_tokens_in']:>8} {s['est_tokens_out']:>8} "
            f"{s['est_usd']:>9.4f} {s['ceiling_usd']:>9.4f} {s['zdr_endpoints_at_snapshot']:>6}"
        )
    lines.append(
        f"TOTAL est_usd={sum(s['est_usd'] for s in summaries):.4f} "
        f"ceiling_usd={sum(s['ceiling_usd'] for s in summaries):.4f}"
    )
    return "\n".join(lines)


def run_dry(models: list[str], style: str) -> list[dict[str, Any]]:
    """Print the plan. Makes no network call and prints no review text."""
    fixtures = unseen_fixtures()
    print(
        f"DRY RUN (no API call, no review text printed): {len(fixtures)} unseen held-out reviews, "
        f"prompt={style}, seed={SEED}, pricing snapshot {PRICING_SNAPSHOT}"
    )
    print(
        f"input est = ceil(chars/{CHARS_PER_TOKEN}) + {MESSAGE_OVERHEAD_TOKENS}/call (conservative); "
        f"output est = {OUT_TOKENS_PLAIN} (non-reasoning) / {OUT_TOKENS_REASONING} (reasoning); "
        f"ceiling = input + max_tokens {MAX_TOKENS_CAP} at output price"
    )
    summaries = []
    for model in models:
        calls = plan_calls(model, fixtures, style)
        summaries.append(summarize_plan(model, calls))
        for c in calls[:3]:
            print(
                f"  [{model}] {c['id']}: chars={c['chars']} est_in={c['est_tokens_in']} "
                f"est_out={c['est_tokens_out']} est_usd={c['est_usd']:.5f}"
            )
        print(f"  [{model}] ... {len(calls) - 3} more calls")
    print(format_plan_table(summaries))
    return summaries


# --------------------------------------------------------------------------- cassettes / provider


def cassette_key(model: str, system: str, user: str) -> str:
    payload = "\x00".join([model, system, user, f"seed={SEED}"])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_payload(model: str, system: str, user: str) -> dict[str, Any]:
    """The exact request body. `provider.zdr` is unconditional (asserted by a test)."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "seed": SEED,
        "max_tokens": MAX_TOKENS_CAP,
        "provider": {"zdr": True},
    }


class ZdrBaselineProvider(SecondaryProvider):
    """SecondaryProvider (OpenRouter, ZDR-only, trains_on_input=False) plus the three knobs a
    baseline needs and the production failover path does not: `seed`, `max_tokens`, and the
    serving provider's name. Reuses the parent's key/model plumbing and privacy contract."""

    async def complete_with_meta(self, system: str, user: str) -> tuple[str, int, int, str | None]:
        import httpx
        from app.core.providers.secondary import _OPENROUTER_CHAT_URL

        assert_privacy_safe(self, "moat-test baseline")
        if not self.is_configured:
            raise RuntimeError("SECONDARY_PROVIDER_API_KEY / model not set")
        payload = build_payload(self._model, system, user)
        assert payload["provider"] == {"zdr": True}  # never send third-party text without it
        async with httpx.AsyncClient(timeout=float(REQUEST_TIMEOUT_SECONDS)) as client:
            resp = await client.post(
                _OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        raw = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        return (
            raw,
            int(usage.get("prompt_tokens", 0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
            data.get("provider"),
        )


def zdr_preflight(model: str) -> int:
    """Count live ZDR endpoints for `model`; raise if none (fail closed). Network: public GET."""
    import httpx

    resp = httpx.get("https://openrouter.ai/api/v1/endpoints/zdr", timeout=60.0)
    resp.raise_for_status()
    n = sum(1 for e in resp.json().get("data", []) if e.get("model_id") == model)
    if n == 0:
        raise SystemExit(f"REFUSING: {model} has no endpoint on the live ZDR allowlist")
    return n


# --------------------------------------------------------------------------- scoring (reused)


def parse_extraction(raw: str) -> dict[str, Any]:
    """Same validation production applies to LLM output (ReviewExtractionLLMOutput)."""
    from app.core.llm import _parse_response

    return _parse_response(raw).model_dump(mode="json")


def score_record(
    fixture: dict[str, Any], raw: str | None, call_error: str | None
) -> dict[str, Any]:
    """One review -> the record shape `_headline_cell` / `coverage.analyze` expect.

    A call error or unparseable output is scored as the schema-default extraction (abstain on
    everything) and flagged `parse_error`; the report prints how many, so a model that fails the
    format is visible rather than silently excluded (production's own scorer EXCLUDES errors from
    its n, which would flatter a baseline that fails often).
    """
    error = call_error
    extraction: dict[str, Any] | None = None
    if raw is not None and error is None:
        try:
            extraction = parse_extraction(raw)
        except Exception as exc:  # noqa: BLE001 -- any parse/validation failure is a scored miss
            error = f"parse_error: {type(exc).__name__}"
    if extraction is None:
        from app.core.schemas import ReviewExtractionLLMOutput

        extraction = ReviewExtractionLLMOutput().model_dump(mode="json")
    scores = {fr.field: fr.score for fr in score_fixture(fixture, extraction)}
    cond = {"field_scores": scores, "predicted": extraction}
    return {
        "id": fixture["id"],
        "unresolved_fields": list(unresolved_fields(fixture)),
        "as_deployed": cond,
        "language_forced": cond,  # _headline_cell reads both; there is no routing in this arm
        "error": error,
    }


def summarize_scores(
    records: list[dict[str, Any]], production: dict[str, dict[str, float]] | None = None
) -> dict[str, Any]:
    """Headline (split-gold excluded), all-pairs, coverage metrics, and paired delta vs production."""
    # Private helper reused on purpose: it IS the published headline's arithmetic.
    from eval.score_held_out_corpus_v2 import _headline_cell

    fields = list(HEADLINE_FIELDS)
    split_excluded = _headline_cell(records, fields, exclude_split=True, exclude_exposed=False)
    all_pairs = _headline_cell(records, fields, exclude_split=False, exclude_exposed=False)
    out: dict[str, Any] = {
        "n": len(records),
        "n_errors": sum(1 for r in records if r.get("error")),
        "headline_split_excluded": {
            "score": split_excluded["as_deployed"]["score"],
            "ci_95": split_excluded["as_deployed"]["ci_95"],
            "n_split_pairs_excluded": split_excluded["n_split_pairs_excluded"],
        },
        "all_pairs": {
            "score": all_pairs["as_deployed"]["score"],
            "ci_95": all_pairs["as_deployed"]["ci_95"],
        },
        "coverage_metrics": coverage.analyze(records, "as_deployed"),
    }
    if production:
        diffs = []
        for r in records:
            prod = production.get(r["id"])
            if prod is None:
                continue
            used = [f for f in fields if f not in r["unresolved_fields"]]
            if used:
                diffs.append(
                    mean(r["as_deployed"]["field_scores"][f] for f in used)
                    - mean(prod[f] for f in used)
                )
        # eval.bootstrap.bootstrap_ci clamps to [0, 1]; a paired DIFFERENCE can be negative.
        out["paired_vs_production"] = _paired_diff_ci(diffs) if diffs else None
    return out


def _paired_diff_ci(diffs: list[float]) -> dict[str, float]:
    import random

    rng = random.Random(SEED)  # noqa: S311 -- resampling, not security
    n = len(diffs)
    means = sorted(mean(diffs[rng.randrange(n)] for _ in range(n)) for _ in range(10_000))
    return {
        "mean_diff_baseline_minus_production": mean(diffs),
        "lower": means[249],
        "upper": means[9749],
        "n": n,
    }


def load_production_scores() -> dict[str, dict[str, float]]:
    data = json.loads(PRODUCTION_RECORDS_PATH.read_text(encoding="utf-8"))
    return {r["id"]: r["as_deployed"]["field_scores"] for r in data["records"]}


# --------------------------------------------------------------------------- live / replay


def _slug(model: str) -> str:
    return model.replace("/", "__").replace(":", "_")


async def run_model(
    model: str, style: str, mode: str, max_usd: float, api_key: str, log_path: Path
) -> list[dict[str, Any]]:
    import app.core.providers.cassette as cassette_module

    cassette_module.CASSETTES_PATH = CASSETTE_PATH
    price = PRICING[model]
    fixtures = unseen_fixtures()
    provider = ZdrBaselineProvider(api_key=api_key, model=model)
    spent = 0.0
    records: list[dict[str, Any]] = []
    for fx in fixtures:
        system, user = build_messages(fx["review_text"], style)
        key = cassette_key(model, system, user)
        raw: str | None = None
        call_error: str | None = None
        t_in = t_out = 0
        served_by: str | None = None
        t0 = time.monotonic()
        status = "replay"
        hit = cassette_module.replay(key)
        if hit is None and mode == "record":
            ceiling = usd(estimate_input_tokens(system, user), MAX_TOKENS_CAP, price)
            if spent + ceiling > max_usd:
                print(f"BUDGET GUARD: stopping before {fx['id']}: spent={spent:.4f}")
                break
        try:
            if hit is not None:
                raw, t_in, t_out = hit
            elif mode == "replay":
                call_error = "no cassette (replay mode)"
            else:
                status = "live"
                raw, t_in, t_out, served_by = await provider.complete_with_meta(system, user)
                cassette_module.record(key, raw, t_in, t_out)
                await asyncio.sleep(PACING_SECONDS)
        except Exception as exc:  # noqa: BLE001 -- logged below, scored as a miss
            call_error = type(exc).__name__
            status = "error"
        finally:
            usd_cost = usd(t_in, t_out, price)
            if status == "live":
                spent += usd_cost
            with log_path.open("a", encoding="utf-8") as fh:  # ids/tokens/cost only, no text
                fh.write(
                    json.dumps(
                        {
                            "ts": now_iso(),
                            "id": fx["id"],
                            "model": model,
                            "served_by": served_by,
                            "status": status,
                            "tokens_in": t_in,
                            "tokens_out": t_out,
                            "usd_cost": usd_cost,
                            "latency_ms": int((time.monotonic() - t0) * 1000),
                            "error": call_error,
                        }
                    )
                    + "\n"
                )
        records.append(score_record(fx, raw, call_error))
    return records


def write_results(model: str, style: str, mode: str, records: list[dict[str, Any]]) -> Path:
    summary = summarize_scores(records, load_production_scores())
    out = RESULTS_DIR / f"moat_test_{_slug(model)}_{style}.json"
    out.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "git_sha": get_git_sha(),
                "model": model,
                "prompt_style": style,
                "mode": mode,
                "seed": SEED,
                "pricing_snapshot": PRICING_SNAPSHOT,
                "scorer": "eval.runner.score_fixture + eval.score_held_out_corpus_v2._headline_cell",
                "headline_fields": list(HEADLINE_FIELDS),
                "summary": summary,
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="plan + exact estimate, NO API call")
    ap.add_argument("--mode", choices=["record", "replay"], help="live+record or cassette replay")
    ap.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    ap.add_argument("--prompt", choices=["plain", "production"], default="plain")
    ap.add_argument("--confirm-spend", action="store_true")
    ap.add_argument("--max-usd", type=float, default=0.0)
    args = ap.parse_args()

    unknown = [m for m in args.models if m not in PRICING]
    if unknown:
        raise SystemExit(f"models not in the pricing table (add with a dated price): {unknown}")
    if args.dry_run or args.mode is None:
        run_dry(args.models, args.prompt)
        return

    api_key = ""
    if args.mode == "record":
        if not args.confirm_spend or args.max_usd <= 0:
            raise SystemExit("record mode needs --confirm-spend and --max-usd > 0")
        from app.core.config import get_settings

        api_key = get_settings().secondary_provider_api_key
        if not api_key:
            raise SystemExit("SECONDARY_PROVIDER_API_KEY (an OpenRouter key) is not set")
        for m in args.models:
            print(f"ZDR pre-flight {m}: {zdr_preflight(m)} endpoint(s)")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for m in args.models:
        log_path = RESULTS_DIR / f"moat_test_calls_{_slug(m)}.jsonl"
        records = asyncio.run(run_model(m, args.prompt, args.mode, args.max_usd, api_key, log_path))
        print(f"{m}: wrote {write_results(m, args.prompt, args.mode, records)}")


if __name__ == "__main__":
    main()
