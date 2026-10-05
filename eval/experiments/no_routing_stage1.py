"""S15d U5c: PREPARED (never run live here) stage 1 of the "no routing" experiment.

Design source: docs/specs/s15c-language-routing.md, S2c stage 1 and its "Stage 1 design
(Session 15d)" section. Question: can the language router be deleted (always send the hi-en
prompt) without losing extraction quality? Stage 1 is a futility screen on English reviews:

  - 27 CI-gate `en` dev fixtures (eval/fixtures/*.json, ground-truth language "en") and
  - the 3 held-out en/en fixtures (gt en, detected en; the only held-out fixtures whose hi-en
    prompt result is not already recorded),

each run through the existing hi-en prompt (`build_prompt(..., "hi-en")`) and compared, paired per
fixture, with the RECORDED en-prompt result (eval/results/latest.json for the CI-gate set,
eval/results/held_out_scoring_v2.json `as_deployed` for the 3 held-out). The metric is the 8-field
headline: the mean over every scored field except `stars` (constant) and `language` (echo/label
noise), the same definition as the published held-out headline. `app/` is not modified.

Modes:
    record   LIVE Groq calls, cassette-recorded. Requires --i-understand-this-spends-quota.
             Budget guard, pacing, day-ledger and resume (see below). NOT run by Session 15d.
    replay   Re-derive the rows from the cassette: zero network, zero quota, no ledger.
    report   Paired deltas + bootstrap CIs + the pre-registered decision rule (pure function of
             the saved rows and recorded artifacts; zero quota).
    design   Zero-quota design numbers: expected tokens, and the verdict probabilities of the
             decision rule as a function of the true English effect (normal approximation).

Safety rails (record mode):
  - per-model token guard: never start a call if used(model, last 24h) + EST_TOKENS_PER_CALL would
    exceed TOKEN_CEILING_PER_MODEL (95K, i.e. 5% inside the 100K/model/day budget; the pools are
    independent, each model's TPD is 200K). An escalated call charges BOTH pools.
  - day ledger (eval/results/no_routing_stage1_ledger.json): a rolling 24h window of every call's
    tokens per model, persisted after every call, so separate invocations cannot exceed the ceiling.
    Rolling rather than calendar day because Groq's TPD window is not guaranteed to reset at 00:00 UTC.
  - pacing: PACING_SECONDS between calls keeps ~3K-token calls under the 8K TPM per-model limit.
  - resume: rows are persisted after every call; a re-run skips ids already done.
  - own cassette file (eval/cassettes/no_routing_stage1_cassettes.json).
  - Do NOT run on the same day as ADR 0031's buy_again day 2: both draw on the same TPD pools
    (this script's ledger does not see that experiment's usage).

Usage:
    uv run python eval/experiments/no_routing_stage1.py design
    EVAL_CASSETTE_MODE=record uv run python eval/experiments/no_routing_stage1.py record \
        --i-understand-this-spends-quota
    uv run python eval/experiments/no_routing_stage1.py replay
    uv run python eval/experiments/no_routing_stage1.py report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.provenance import get_git_sha, now_iso  # noqa: E402
from eval.runner import score_fixture  # noqa: E402

FIXTURES_DIR = ROOT / "eval" / "fixtures"
HELD_OUT_DIR = FIXTURES_DIR / "_held_out_hindi_hinglish"
LATEST_PATH = ROOT / "eval" / "results" / "latest.json"
HELD_OUT_ARTIFACT = ROOT / "eval" / "results" / "held_out_scoring_v2.json"
CASSETTE_PATH = ROOT / "eval" / "cassettes" / "no_routing_stage1_cassettes.json"
ROWS_PATH = ROOT / "eval" / "results" / "no_routing_stage1_rows.json"
LEDGER_PATH = ROOT / "eval" / "results" / "no_routing_stage1_ledger.json"
REPORT_PATH = ROOT / "eval" / "results" / "no_routing_stage1_report.json"
DESIGN_PATH = ROOT / "eval" / "results" / "no_routing_stage1_design.json"

# ---- budget (spec S2c: 100K tokens per model per day; each pool independent, TPD is 200K) ----
TOKEN_CEILING_PER_MODEL = 95_000  # 5% inside the 100K/model/day experiment budget
EST_TOKENS_PER_CALL = 3_200  # ~p95 of the measured small-tier tokens per extraction (3,265)
PACING_SECONDS = 28.0  # ~3K-token calls under the 8K TPM per-model limit
LEDGER_WINDOW_SECONDS = 24 * 3600
N_CI_GATE_EN = 27  # spec: 27 CI-gate `en` dev fixtures
N_HELD_OUT_EN = 3  # spec: 3 unrecorded held-out en/en fixtures

# ---- statistics / decision rule (spec S2c, pre-registered) ----
N_RESAMPLES = 10_000
SEED = 42
EXCLUDED_FIELDS = ("stars", "language")  # same exclusion as the published headline
NON_INFERIORITY_MARGIN = -0.03  # adopt only if lower 95% bound of (unified - routed) >= -3pp
FUTILITY_MEAN = -0.05  # stop if the mean paired 8-field delta is below -5pp
Z95 = 1.96


@dataclass(frozen=True)
class CallResult:
    """What one extraction call returns to the harness (provider-agnostic, easy to mock)."""

    extraction: dict[str, Any]
    model: str
    tokens_in: int
    tokens_out: int
    escalated: bool = False
    degraded: bool = False


Caller = Callable[[str], Awaitable[CallResult]]


@dataclass(frozen=True)
class Item:
    """One fixture to run, with the recorded en-prompt extraction it is compared against."""

    fid: str
    stratum: str  # "ci_gate_en" | "held_out_en"
    fixture: dict[str, Any]
    baseline: dict[str, Any]  # recorded en-prompt extraction (dict of field -> predicted)
    recorded_scores: dict[str, float]  # the field scores recorded with it (drift check)


@dataclass
class StageOutcome:
    """Result of one record/replay invocation."""

    n_already_done: int = 0
    n_run: int = 0
    stopped_by_guard: bool = False
    stopped_by_error: str | None = None
    next_id: str | None = None
    used_by_model_window: dict[str, int] = field(default_factory=dict)


# ----------------------------------------------------------------------------------------
# Plan: which fixtures, and what they are compared against
# ----------------------------------------------------------------------------------------


def _extraction_from_latest_fields(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild the extraction dict the CI-gate run produced from its recorded per-field output."""
    return {f["field"]: f["predicted"] for f in fields}


def load_plan(
    fixtures_dir: Path = FIXTURES_DIR,
    latest_path: Path = LATEST_PATH,
    held_out_path: Path = HELD_OUT_ARTIFACT,
    *,
    expected: tuple[int, int] | None = (N_CI_GATE_EN, N_HELD_OUT_EN),
) -> list[Item]:
    """Build the ordered item list (CI-gate en first, then held-out en/en), ids sorted.

    Raises ValueError if the set is not the one the spec describes (27 + 3) so a changed fixture
    directory cannot silently change what the experiment measures. `expected=None` disables the
    size check (unit tests use tiny synthetic sets).
    """
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    recorded = {f["id"]: f for f in latest["fixtures"]}
    items: list[Item] = []
    for p in sorted(fixtures_dir.glob("*.json")):
        if p.name.startswith("."):
            continue
        fx = json.loads(p.read_text(encoding="utf-8"))
        if fx.get("ground_truth", {}).get("language") != "en":
            continue
        rec = recorded.get(fx["id"])
        if rec is None or rec.get("error"):
            raise ValueError(f"CI-gate en fixture {fx['id']} has no clean recorded result")
        items.append(
            Item(
                fx["id"],
                "ci_gate_en",
                fx,
                _extraction_from_latest_fields(rec["fields"]),
                {f["field"]: f["score"] for f in rec["fields"]},
            )
        )

    held = json.loads(held_out_path.read_text(encoding="utf-8"))
    gold = {}
    for p in sorted((fixtures_dir / "_held_out_hindi_hinglish").glob("hien-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        gold[d["id"]] = d
    for r in sorted(held["records"], key=lambda r: r["id"]):
        if r["gt_language"] == "en" and r["detected_language"] == "en":
            items.append(
                Item(
                    r["id"],
                    "held_out_en",
                    gold[r["id"]],
                    r["as_deployed"]["predicted"],
                    r["as_deployed"]["field_scores"],
                )
            )

    if expected is not None:
        n_ci = sum(1 for i in items if i.stratum == "ci_gate_en")
        n_ho = sum(1 for i in items if i.stratum == "held_out_en")
        if (n_ci, n_ho) != expected:
            raise ValueError(f"plan is {n_ci}+{n_ho} fixtures, spec says {expected}")
    return items


def headline_score(fixture: dict[str, Any], extraction: dict[str, Any]) -> float:
    """The 8-field headline for one fixture: mean of the field scores excluding stars/language."""
    scores = [
        fr.score for fr in score_fixture(fixture, extraction) if fr.field not in EXCLUDED_FIELDS
    ]
    return mean(scores) if scores else 0.0


def build_stage_prompt(review_text: str) -> str:
    """The hi-en prompt exactly as production builds it (sanitize, wrap, build_prompt)."""
    from app.core.prompts import build_prompt
    from app.core.sanitize import sanitize, wrap_for_llm

    clean, _ = sanitize(review_text)
    return build_prompt(wrap_for_llm(clean), "hi-en")


# ----------------------------------------------------------------------------------------
# Day ledger: cannot exceed the daily ceiling across invocations
# ----------------------------------------------------------------------------------------


class DayLedger:
    """Persistent rolling-24h ledger of tokens spent per model."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        if path.exists() and path.read_text(encoding="utf-8").strip():
            self.entries = json.loads(path.read_text(encoding="utf-8"))["entries"]

    def used(self, model: str, now: float) -> int:
        """Tokens charged to `model` inside the trailing 24h window ending at `now`."""
        cutoff = now - LEDGER_WINDOW_SECONDS
        return sum(e["tokens"] for e in self.entries if e["model"] == model and e["ts"] > cutoff)

    def add(self, model: str, tokens: int, now: float, fid: str) -> None:
        """Charge tokens and persist immediately (a crash after the call must not lose it)."""
        self.entries.append({"ts": now, "model": model, "tokens": tokens, "id": fid})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": self.entries}, indent=2) + "\n", encoding="utf-8"
        )


def would_exceed(
    ledger: DayLedger, models: list[str], now: float, ceiling: int, est: int
) -> str | None:
    """Return the first model whose pool would pass `ceiling` after one more estimated call."""
    for m in models:
        if ledger.used(m, now) + est > ceiling:
            return m
    return None


# ----------------------------------------------------------------------------------------
# Record / replay
# ----------------------------------------------------------------------------------------


def _load_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["rows"]}


def _save_rows(path: Path, rows: dict[str, dict[str, Any]], mode: str) -> None:
    payload = {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "spec": "docs/specs/s15c-language-routing.md (Stage 1 design, Session 15d)",
        "mode_last_written": mode,
        "rows": [rows[k] for k in sorted(rows)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def run_stage(
    items: list[Item],
    caller: Caller,
    *,
    mode: str,
    rows_path: Path,
    ledger_path: Path,
    models: list[str],
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ceiling: int = TOKEN_CEILING_PER_MODEL,
    est: int = EST_TOKENS_PER_CALL,
    pacing: float = PACING_SECONDS,
) -> StageOutcome:
    """Run (or replay) every not-yet-done item; guard, pace, persist after each call.

    mode="record": ledger guard + pacing apply. mode="replay": neither (no quota is touched).
    A caller RuntimeError (provider exhausted / quota) stops the run cleanly; rows so far are kept.
    """
    if mode not in ("record", "replay"):
        raise ValueError(f"mode must be record or replay, got {mode!r}")
    rows = _load_rows(rows_path)
    ledger = DayLedger(ledger_path)
    out = StageOutcome(n_already_done=sum(1 for i in items if i.fid in rows))
    pending = [i for i in items if i.fid not in rows]
    for idx, item in enumerate(pending):
        if mode == "record":
            over = would_exceed(ledger, models, now_fn(), ceiling, est)
            if over is not None:
                out.stopped_by_guard = True
                out.next_id = item.fid
                break
        try:
            res = await caller(build_stage_prompt(item.fixture["review_text"]))
        except RuntimeError as exc:
            out.stopped_by_error = str(exc)
            out.next_id = item.fid
            break
        now = now_fn()
        if mode == "record":
            spent = res.tokens_in + res.tokens_out
            ledger.add(res.model, spent, now, item.fid)
            if res.escalated:
                # The small-tier attempt also spent tokens; charge it as the same size.
                ledger.add(models[0], spent, now, item.fid)
        rows[item.fid] = {
            "id": item.fid,
            "stratum": item.stratum,
            "predicted": res.extraction,
            "model": res.model,
            "escalated": res.escalated,
            "degraded": res.degraded,
            "tokens_in": res.tokens_in,
            "tokens_out": res.tokens_out,
        }
        _save_rows(rows_path, rows, mode)
        out.n_run += 1
        if mode == "record" and idx < len(pending) - 1:
            await sleep_fn(pacing)
    out.used_by_model_window = {m: ledger.used(m, now_fn()) for m in models}
    return out


def make_live_caller() -> Caller:
    """Production-path caller (tiered router, no Gemini fallback). Only used in record/replay."""
    from app.core.config import get_settings
    from app.core.llm import _SYSTEM_PROMPT
    from app.core.router import route_extraction

    settings = get_settings()

    async def _call(prompt: str) -> CallResult:
        out, model, t_in, t_out, escalated, degraded = await route_extraction(
            prompt, _SYSTEM_PROMPT, allow_gemini_fallback=False, settings=settings
        )
        return CallResult(out.model_dump(), model, t_in, t_out, escalated, degraded)

    return _call


# ----------------------------------------------------------------------------------------
# Statistics and the pre-registered decision rule
# ----------------------------------------------------------------------------------------


def paired_bootstrap_ci(deltas: list[float]) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of mean(deltas): 10,000 resamples, seed 42, NOT clamped.

    (eval.bootstrap.bootstrap_ci clamps to [0, 1], which would destroy a CI on a negative delta.)
    Paired = the resampling unit is the fixture, and each delta is already unified minus routed.
    """
    if len(deltas) < 2:
        raise ValueError("need at least 2 deltas for a bootstrap CI")
    rng = random.Random(SEED)  # noqa: S311 -- statistical resampling, not security
    n = len(deltas)
    means = sorted(mean(rng.choices(deltas, k=n)) for _ in range(N_RESAMPLES))
    return means[int(0.025 * N_RESAMPLES)], means[int(0.975 * N_RESAMPLES) - 1]


def delta_stats(deltas: list[float]) -> dict[str, Any]:
    """Mean, bootstrap CI, SD and up/down/zero counts of a paired-delta list (fractions, not pp)."""
    if not deltas:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": len(deltas),
        "mean": mean(deltas),
        "n_up": sum(1 for d in deltas if d > 1e-12),
        "n_down": sum(1 for d in deltas if d < -1e-12),
        "n_zero": sum(1 for d in deltas if abs(d) <= 1e-12),
    }
    if len(deltas) >= 2:
        lo, hi = paired_bootstrap_ci(deltas)
        sd = pstdev(deltas) * math.sqrt(len(deltas) / (len(deltas) - 1))  # sample SD
        out.update({"ci95": [lo, hi], "sd": sd, "se": sd / math.sqrt(len(deltas))})
    return out


def english_deltas(items: list[Item], rows: dict[str, dict[str, Any]]) -> dict[str, list[float]]:
    """Per-stratum (hi-en prompt minus recorded en prompt) 8-field deltas for finished items."""
    by_stratum: dict[str, list[float]] = {"ci_gate_en": [], "held_out_en": []}
    for it in items:
        row = rows.get(it.fid)
        if row is None:
            continue
        new = headline_score(it.fixture, row["predicted"])
        old = headline_score(it.fixture, it.baseline)
        by_stratum[it.stratum].append(new - old)
    return by_stratum


def baseline_drift(items: list[Item]) -> list[str]:
    """Ids whose recorded en-prompt field scores no longer equal a re-score with today's scorer.

    The paired delta compares NEW predictions (scored by the current scorer) with RECORDED ones;
    if the scorer has moved since they were recorded, the delta would mix scorer change into the
    prompt effect. The baseline is therefore re-scored from its saved predictions, and this
    reports any fixture where that differs from what was recorded (expected: none).
    """
    drift = []
    for it in items:
        now = {fr.field: fr.score for fr in score_fixture(it.fixture, it.baseline)}
        if any(abs(now[f] - it.recorded_scores[f]) > 1e-9 for f in now if f in it.recorded_scores):
            drift.append(it.fid)
    return drift


def hinglish_recorded_deltas(held_out: dict[str, Any]) -> list[float]:
    """Hinglish stratum from RECORDED data only: gt hi-en fixtures, hi-en prompt minus as deployed.

    For gt hi-en the `language_forced` condition IS the hi-en prompt (and equals as_deployed where
    the detector already chose hi-en), so this is the "no routing" arm; `as_deployed` is routed.
    """
    out = []
    for r in held_out["records"]:
        if r["gt_language"] != "hi-en":
            continue
        scores = {
            c: mean(v for f, v in r[c]["field_scores"].items() if f not in EXCLUDED_FIELDS)
            for c in ("as_deployed", "language_forced")
        }
        out.append(scores["language_forced"] - scores["as_deployed"])
    return out


def decide(
    english: dict[str, Any], hinglish: dict[str, Any], n_expected_english: int
) -> dict[str, Any]:
    """Apply the pre-registered rule and say what stage 1 can and cannot conclude."""
    en_complete = english.get("n", 0) >= n_expected_english
    checks: dict[str, Any] = {
        "english_mean_above_futility_floor": (
            english["mean"] >= FUTILITY_MEAN if english.get("n") else None
        ),
        "english_lower_bound_meets_margin": (
            english["ci95"][0] >= NON_INFERIORITY_MARGIN if english.get("ci95") else None
        ),
        "hinglish_lower_bound_meets_margin": (
            hinglish["ci95"][0] >= NON_INFERIORITY_MARGIN if hinglish.get("ci95") else None
        ),
        "english_complete": en_complete,
    }
    if not english.get("n"):
        verdict = "NO DATA: no English rows recorded yet"
    elif english["mean"] < FUTILITY_MEAN:
        verdict = (
            "STOP (futility): mean paired 8-field delta is below -5pp; routing stays and the "
            "detector-quality question becomes the conversation instead"
        )
    elif not en_complete:
        verdict = "INTERIM: fewer than the planned English fixtures are recorded; judge at n=30"
    elif checks["english_lower_bound_meets_margin"] and checks["hinglish_lower_bound_meets_margin"]:
        verdict = (
            "NON-INFERIORITY NUMERICALLY MET on stage-1 data, but NOT sufficient to delete routing: "
            "the arm is the existing hi-en prompt (not an authored unified prompt), the English "
            "stratum is n=30, and the Hinglish stratum adds no new data. Proceed to stage 2."
        )
    else:
        verdict = (
            "NOT DECIDED: futility not triggered, but the lower 95% bound is below -3pp on at least "
            "one stratum, so non-inferiority is not certified. Expected at n=30 (the CI half-width "
            "is about 4pp); this is a futility screen, not a proof. Stage 2 is the only way to decide."
        )
    return {
        "rule": {
            "adopt_no_routing_only_if": "lower 95% bound of (unified - routed) >= -3pp on the "
            "8-field headline for BOTH strata (Hinglish, English)",
            "stage1_futility": "mean paired 8-field delta (hi-en minus en) below -5pp: stop",
        },
        "checks": checks,
        "verdict": verdict,
        "stage1_cannot_certify": (
            "Hinglish stratum: the unified prompt does not exist yet, and the recorded hi-en-prompt "
            "arm is the status-quo prompt for those reviews. English stratum: at n=30 the standard "
            "error is about 2.0-2.9pp (per-fixture delta SD 11.2pp over all 106 held-out fixtures, "
            "15.8pp over the 53 whose two runs differ; `design` mode), so a -3pp margin is only "
            "certifiable if the observed mean is about +1.0pp to +2.7pp or better; the CI-gate set "
            "overlaps the en prompt's few-shots, which favours "
            "the routed (en) side and so makes this test conservative for no-routing."
        ),
    }


def build_report(
    items: list[Item], rows: dict[str, dict[str, Any]], held_out: dict[str, Any]
) -> dict[str, Any]:
    """Pure function of the saved rows and the recorded artifacts (no quota, no network)."""
    by_stratum = english_deltas(items, rows)
    pooled = by_stratum["ci_gate_en"] + by_stratum["held_out_en"]
    english = delta_stats(pooled)
    hinglish = delta_stats(hinglish_recorded_deltas(held_out))
    tokens = {
        "rows": len(rows),
        "tokens_total": sum(r["tokens_in"] + r["tokens_out"] for r in rows.values()),
        "by_final_model": {
            m: sum(r["tokens_in"] + r["tokens_out"] for r in rows.values() if r["model"] == m)
            for m in sorted({r["model"] for r in rows.values()})
        },
        "n_escalated": sum(1 for r in rows.values() if r["escalated"]),
        "n_degraded": sum(1 for r in rows.values() if r["degraded"]),
    }
    return {
        "generated_at": now_iso(),
        "git_sha": get_git_sha(),
        "method": (
            f"paired percentile bootstrap, {N_RESAMPLES} resamples, seed {SEED}, unclamped; "
            "metric = 8-field headline (all scored fields except stars, language)"
        ),
        "baseline_rescore_drift_ids": baseline_drift(items),
        "english_pooled_hi_en_prompt_minus_en_prompt": english,
        "english_by_stratum": {k: delta_stats(v) for k, v in by_stratum.items()},
        "hinglish_recorded_hi_en_prompt_minus_as_deployed": hinglish,
        "decision": decide(english, hinglish, N_CI_GATE_EN + N_HELD_OUT_EN),
        "tokens_spent_by_this_experiment": tokens,
    }


def _pp(x: float) -> str:
    return f"{x * 100:+.2f}pp"


def print_report(rep: dict[str, Any]) -> None:
    en = rep["english_pooled_hi_en_prompt_minus_en_prompt"]
    hi = rep["hinglish_recorded_hi_en_prompt_minus_as_deployed"]
    print(rep["method"])
    for name, s in (("English (pooled, NEW)", en), ("Hinglish (recorded)", hi)):
        if not s.get("n"):
            print(f"{name}: no data")
            continue
        ci = f" 95% CI [{_pp(s['ci95'][0])}, {_pp(s['ci95'][1])}]" if s.get("ci95") else ""
        print(
            f"{name}: n={s['n']} mean {_pp(s['mean'])}{ci}"
            f" (up {s['n_up']}/down {s['n_down']}/zero {s['n_zero']})"
        )
    for k, s in rep["english_by_stratum"].items():
        if s.get("n"):
            print(f"  {k}: n={s['n']} mean {_pp(s['mean'])}")
    print("baseline drift ids (expected none):", rep["baseline_rescore_drift_ids"])
    print("VERDICT:", rep["decision"]["verdict"])
    print("CANNOT CERTIFY:", rep["decision"]["stage1_cannot_certify"])
    print("tokens spent:", rep["tokens_spent_by_this_experiment"])


# ----------------------------------------------------------------------------------------
# Zero-quota design numbers
# ----------------------------------------------------------------------------------------


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def verdict_probabilities(true_mean: float, sd: float, n: int) -> dict[str, float]:
    """P(stage-1 verdict | true English effect), normal approximation to the sample mean.

    futility: mean < -5pp; non-inferiority met: mean - 1.96*SE >= -3pp; else not decided.
    (The percentile bootstrap at n=30 is close to this but not identical; treat as approximate.)
    """
    se = sd / math.sqrt(n)
    p_fut = _phi((FUTILITY_MEAN - true_mean) / se)
    ni_threshold = NON_INFERIORITY_MARGIN + Z95 * se
    p_ni = 1.0 - _phi((ni_threshold - true_mean) / se)
    return {"futility": p_fut, "non_inferior": p_ni, "not_decided": 1.0 - p_fut - p_ni}


def design(held_out: dict[str, Any], token_cost: dict[str, Any]) -> dict[str, Any]:
    """Expected tokens and verdict probabilities, from recorded data only."""
    n = N_CI_GATE_EN + N_HELD_OUT_EN
    deltas = []
    for r in held_out["records"]:
        s = {
            c: mean(v for f, v in r[c]["field_scores"].items() if f not in EXCLUDED_FIELDS)
            for c in ("as_deployed", "language_forced")
        }
        deltas.append(s["language_forced"] - s["as_deployed"])
    nonzero = [d for d in deltas if abs(d) > 1e-12]
    sd_all = pstdev(deltas) * math.sqrt(len(deltas) / (len(deltas) - 1))
    sd_nonzero = pstdev(nonzero) * math.sqrt(len(nonzero) / (len(nonzero) - 1))
    small = token_cost["per_tier"]["small"]["tokens_total"]["mean"]
    large = token_cost["per_tier"]["large"]["tokens_total"]["mean"]
    f_large = token_cost["tier_mix_observed"]["large"]["fraction"]
    return {
        "n_calls": n,
        "expected_tokens": {
            "small_pool": n * small,  # every call is attempted on the small tier first
            "large_pool": n * f_large * large,  # only escalated calls reach the large tier
            "note": "an escalated call charges both pools; the per-pool maximum is the small pool",
            "ceiling_per_model": TOKEN_CEILING_PER_MODEL,
        },
        "delta_sd_held_out": {
            "all_106": sd_all,
            "nonzero_only": sd_nonzero,
            "n_nonzero": len(nonzero),
        },
        "verdict_probabilities_by_true_effect": {
            f"{mu * 100:+.0f}pp": {
                "sd_all_106": verdict_probabilities(mu, sd_all, n),
                "sd_nonzero_only": verdict_probabilities(mu, sd_nonzero, n),
            }
            for mu in [x / 100 for x in (-8, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3)]
        },
        "se_at_n30": {
            "sd_all_106": sd_all / math.sqrt(n),
            "sd_nonzero_only": sd_nonzero / math.sqrt(n),
        },
        "min_observed_mean_to_certify": {
            "sd_all_106": NON_INFERIORITY_MARGIN + Z95 * sd_all / math.sqrt(n),
            "sd_nonzero_only": NON_INFERIORITY_MARGIN + Z95 * sd_nonzero / math.sqrt(n),
        },
    }


# ----------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", choices=["record", "replay", "report", "design"])
    ap.add_argument(
        "--i-understand-this-spends-quota",
        action="store_true",
        help="required for `record`: ~30 live calls, ~75K tokens in the busiest pool",
    )
    args = ap.parse_args(argv)

    held_out = json.loads(HELD_OUT_ARTIFACT.read_text(encoding="utf-8"))
    if args.mode == "design":
        token_cost = json.loads(
            (ROOT / "eval" / "results" / "token_cost_measurement_n106.json").read_text(
                encoding="utf-8"
            )
        )
        result = {
            "generated_at": now_iso(),
            "git_sha": get_git_sha(),
            "inputs": [
                "eval/results/held_out_scoring_v2.json",
                "eval/results/token_cost_measurement_n106.json",
            ],
            **design(held_out, token_cost),
        }
        DESIGN_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        print(f"Written {DESIGN_PATH}")
        return 0

    items = load_plan()
    if args.mode == "report":
        rep = build_report(items, _load_rows(ROWS_PATH), held_out)
        print_report(rep)
        if rep["english_pooled_hi_en_prompt_minus_en_prompt"].get("n"):
            REPORT_PATH.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
            print(f"Written {REPORT_PATH}")
        return 0

    if args.mode == "record" and not args.i_understand_this_spends_quota:
        ap.error("record makes live Groq calls; pass --i-understand-this-spends-quota")
    import app.core.providers.cassette as cassette_module
    from app.core.config import get_settings

    os.environ["EVAL_CASSETTE_MODE"] = args.mode
    cassette_module.CASSETTES_PATH = CASSETTE_PATH
    settings = get_settings()
    outcome = asyncio.run(
        run_stage(
            items,
            make_live_caller(),
            mode=args.mode,
            rows_path=ROWS_PATH,
            ledger_path=LEDGER_PATH,
            models=[settings.groq_model_small, settings.groq_model_large],
        )
    )
    print(outcome)
    if outcome.stopped_by_guard:
        print(
            "BUDGET GUARD tripped: re-run after the 24h window frees tokens (resume is automatic)"
        )
        return 2
    if outcome.stopped_by_error:
        print(f"STOPPED on provider error: {outcome.stopped_by_error}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
