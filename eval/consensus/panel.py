"""LLM judge panel for eval-fixture consensus ground truth -- Groq, dedicated benchmark key.

Originally a 3-candidate panel; now 2 (Session 8 P2 -- see assert_no_self_judging() below).
Panel composition (queried live via Groq's `/v1/models` endpoint -- `owned_by` field --
the same technique `benchmark/vernacular_v2/multi_llm_labeler.py` already uses to confirm
family/owner rather than assuming from naming):

  1. qwen/qwen3.6-27b     (owned_by: Alibaba Cloud) -- Alibaba Qwen family
  2. allam-2-7b           (owned_by: SDAIA)   -- Saudi Data & AI Authority's ALLaM family

  (openai/gpt-oss-120b, owned_by: OpenAI -- OpenAI GPT-OSS family -- REMOVED, Session 8 P2:
  it is review-iq's own production `groq_model_large`, a self-judging conflict. See
  assert_no_self_judging() and docs/architecture/adr/0013-*.md.)

Why NOT `llama-3.3-70b-versatile` (used by `multi_llm_labeler.py`'s original 3-judge
panel): that model is review-iq's OWN production tiered-router large-tier extraction
model (`app/core/config.py`). Using it to judge extraction-quality ground truth for
THIS eval set would be the model judging itself -- a real conflict of interest for
this specific use case (it was fine for the unrelated vernacular SENT/URG/LANG
classification benchmark `multi_llm_labeler.py` was built for, but not here). Excluded
entirely, not just de-weighted.

Why NOT Gemini (`GEMINI_API_KEY`): that key is wired into production as the
SecondaryProvider failover model (`app/core/llm.py::_call_gemini`, gated by
`ENABLE_GEMINI_FALLBACK`) -- it is used for real customer traffic, not a benchmark-only
key. The task's constraint is explicit: no live calls against a production key. Using
it here would risk exactly the kind of quota/traffic collision that already happened
once with Groq's prod key on 2026-07-07 (see `benchmark_groq_key.py`'s docstring).
`multi_llm_labeler.py` mentions Gemini returned `limit: 0` on this project's key at the
time it was tried; regardless of whether that billing gap persists, this key is out of
scope for this labeler on the "no prod-traffic key" constraint alone -- not re-verified
here, since it wouldn't change the decision either way.

Residual risk, documented rather than hidden: ALLaM-2-7B's own technical materials
describe it as initialized from a Llama-2 checkpoint with extensive continued
pretraining on Arabic+English corpora by SDAIA (a different organization, different
corpus emphasis, different base version -- Llama-2, not the Llama-3.3-70b-versatile
actually used in prod). This is NOT "zero shared ancestry" with Meta Llama, but it is
categorically different from the flagged conflict (using the literal production model,
or an undistinguishable variant of it, to judge its own output). It is also the
smallest model on the panel (7B vs 120B/27B) -- see calibration.py, which will drop it
from the active panel for this run if it fails the unambiguous control-set check.

CALIBRATION OUTCOME (this run, see eval/consensus/results/calibration_report.json for
the full data): `allam-2-7b` FAILED calibration reproducibly across two independent
runs (9/33 control-set field checks wrong both times -- same items, same fields:
missed the mixed-sentiment case cal-012, omitted the `language` key entirely on the
Hindi case cal-004, missed the explicit-refund-demand urgency=high case cal-013's
sibling cal-007, among others) and was DROPPED from the active panel, per the explicit
"do not silently keep a failing judge" instruction -- not tuned around, not given a
second chance beyond the one clean rerun needed to rule out temperature=0 run-to-run
noise on Groq's shared infra. `qwen/qwen3.6-27b` initially also failed (10/33 misses)
but that was traced to a real call-configuration bug, not a judgment problem: its Groq
deployment defaults to a hybrid "thinking" mode that was exhausting the completion-
token budget on its reasoning trace before ever emitting JSON (Groq error: "max
completion tokens reached before generating a valid document", confirmed via a direct
API call, not assumed) -- `reasoning_effort="none"` (see its `extra_params` below) and
a larger `max_completion_tokens` fixed this; on rerun it passed with 0/33 misses.

Net result: the ACTIVE PANEL for this labeling run is 2 judges (`openai/gpt-oss-120b`,
`qwen/qwen3.6-27b`), not 3. With exactly 2 raters, "majority" and "unanimous" collapse
into the same case (both must agree, or it's split) -- eval/consensus/voting.py's
generic vote-counting logic already handles this correctly without special-casing (it
was written against however many judges actually respond, not a hardcoded 3), but it
is an honest, load-bearing consequence of the calibration gate actually being enforced,
not a design flaw to paper over. Restoring a genuine 3rd independent-lineage judge
would require either a different Groq-hosted model becoming available on the free
tier, or spending money on a paid provider -- both out of scope for this run.

Every model is called via the dedicated benchmark Groq key (`benchmark_groq_key.py`),
never `GROQ_API_KEY` (prod). No model sees another's answer -- independent concurrent
calls, no shared conversation context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.core.schemas import ReviewExtractionLLMOutput  # noqa: E402
from benchmark.vernacular_v2.benchmark_groq_key import load_benchmark_groq_key  # noqa: E402
from pydantic import ValidationError  # noqa: E402

JUDGE_MODELS: tuple[dict[str, str], ...] = (
    # `openai/gpt-oss-120b` REMOVED (Session 8 P2, review-iq): it is review-iq's own
    # current production `groq_model_large` -- the exact self-judging conflict this
    # file's docstring already warned against for the model that held that role
    # before it (`llama-3.3-70b-versatile`), which was never re-checked when
    # production migrated. See assert_no_self_judging() below and docs/architecture/
    # adr/0013-*.md for the full incident and its measured effect on real results.
    # Net effect: the candidate roster below has exactly ONE judge with a clean
    # calibration pass as of this run (`qwen/qwen3.6-27b`) -- `allam-2-7b` failed
    # calibration independently (see the CALIBRATION OUTCOME note above), so no
    # inter-rater kappa is computable until a genuine third, disjoint, calibration-
    # passing judge is found. Sourcing one is a P3 prerequisite (a held-out corpus's
    # own panel needs this too), not solved here.
    {
        "id": "qwen/qwen3.6-27b",
        "provider": "groq",
        "family": "Alibaba Qwen",
        "owner": "Alibaba Cloud",
        # Qwen3.6's Groq deployment defaults to hybrid "thinking" mode, which was
        # observed burning the entire completion-token budget on its reasoning
        # trace before ever emitting the requested JSON (Groq error: "max
        # completion tokens reached before generating a valid document" --
        # verified interactively, not assumed). `reasoning_effort="none"` disables
        # thinking for this model family; without it, EVERY field on an affected
        # item registers as a miss (total parse failure, not a judgment error) --
        # this is a call-configuration bug, not evidence the model can't judge.
        "extra_params": {"reasoning_effort": "none"},
    },
    {"id": "allam-2-7b", "provider": "groq", "family": "SDAIA ALLaM", "owner": "SDAIA"},
    # Session 8 P3: candidate second judge for the held-out Hindi/Hinglish corpus (needs
    # 2+ judges for real Krippendorff/Fleiss stats -- one calibration-passing, disjoint
    # judge alone can't produce inter-rater agreement). Confirmed via a live, free
    # `/v1/models` listing call (no completion cost) that Groq's current catalog has no
    # OTHER task-appropriate, non-OpenAI-lineage text model: the only other candidates
    # are TTS (canopylabs/orpheus-*), prompt-injection classifiers (meta-llama/llama-
    # prompt-guard-2-*), speech-to-text (whisper-*), OpenAI-family models (excluded on
    # principle), or Groq's own compound/compound-mini (unclear internal model lineage,
    # not used without verifying what it's built on first). qwen/qwen3.8-27b is the
    # same VENDOR/family as qwen/qwen3.6-27b above (Alibaba Qwen) -- not a fully
    # cross-vendor-disjoint judge, but a genuinely different model checkpoint/version,
    # which is materially better than zero independent variance. Disclosed plainly:
    # agreement between qwen3.6 and qwen3.8 is weaker evidence of correctness than
    # agreement between two unrelated vendors would be, same caution class as (but
    # smaller in degree than) the gpt-oss-120b contamination this session already found
    # and fixed. See docs/architecture/adr/0015-*.md.
    {
        "id": "qwen/qwen3.8-27b",
        "provider": "groq",
        "family": "Alibaba Qwen",
        "owner": "Alibaba Cloud",
        # Applying the same reasoning_effort fix qwen3.6-27b needed, preemptively --
        # if qwen3.8 doesn't have the same hybrid-thinking default this is a no-op;
        # calibration will show a real miss pattern if this assumption is wrong.
        "extra_params": {"reasoning_effort": "none"},
    },
)


def assert_no_self_judging(judge_models: tuple[dict[str, str], ...] = JUDGE_MODELS) -> None:
    """Raises if any candidate judge IS review-iq's own current production model.

    Session 8 P2 incident, found by direct verification (not assumed): this exact
    exclusion rule was documented above for `llama-3.3-70b-versatile` (the production
    large-tier model AT THE TIME this panel was built) and correctly kept off
    JUDGE_MODELS -- but the check was a one-time, hardcoded design decision, not a
    runtime invariant. When production migrated to openai/gpt-oss-20b/120b (Groq's
    2026-08-16 deprecation), nothing re-checked the panel roster against the new
    production models, and `openai/gpt-oss-120b` -- review-iq's own `groq_model_large`
    -- had already been on JUDGE_MODELS from the start for an unrelated reason (it was
    added as a judge candidate before that migration, when it wasn't yet a production
    model). The result: half the "blind independent" panel was, for roughly a month of
    real usage, review-iq's own production model judging its own output. Verified
    effect on real data (docs/architecture/adr/0013-*.md): this judge's vote matched
    the model-under-test's exact hedge value in 9/9 sentiment real-hedge disagreements
    it saw -- a rate far more consistent with "same model, same reasoning pattern"
    than independent judgment, and it single-handedly drove Session 7 P2's now-retracted
    "sentiment has zero recoverable headroom" conclusion.

    This function makes the exclusion a live check instead of institutional memory: it
    is called from get_active_panel() (run_consensus.py) before every real labeling
    run, and fails loudly -- not a warning, not a silent drop -- if any candidate
    judge's id matches the CURRENT production groq_model_small/groq_model_large. A
    future model migration will raise here immediately rather than silently
    reintroducing the same conflict under a new model name.
    """
    # Imported lazily (not at module top) so this module stays importable without a
    # full app/ settings environment for callers that only need JUDGE_MODELS/prompts,
    # matching this file's existing lazy-import-at-use-site style (see call_judge()).
    from app.core.config import get_settings

    settings = get_settings()
    production_models = {settings.groq_model_small, settings.groq_model_large}
    conflicts = [m for m in judge_models if m["id"] in production_models]
    if conflicts:
        conflict_ids = [m["id"] for m in conflicts]
        raise ValueError(
            f"Self-judging conflict: judge candidate(s) {conflict_ids} are review-iq's own "
            f"current production model(s) ({sorted(production_models)}). A judge must never "
            "be the same model the extraction pipeline under test actually runs -- remove "
            "the conflicting candidate(s) from JUDGE_MODELS, or replace them, before running "
            "any consensus labeling. See this function's docstring and docs/architecture/"
            "adr/0013-*.md for the incident this check exists to prevent from recurring."
        )


# Deliberately NOT app/core/prompts/en.py's field definitions/worked examples -- that
# prompt (and its escalation heuristics like "pain beats fit") is itself part of what
# the extraction pipeline is being evaluated against. An independent judge needs
# independent instructions, not just a different model answering the identical prompt
# design that's under test. These definitions describe the SAME schema semantics
# (stars vs stars_inferred, urgency tiers) the existing fixtures already use, in
# plainer, example-free language.
JUDGE_SYSTEM_PROMPT = """\
You are labeling a customer product review for a research ground-truth dataset.
Return ONLY a valid JSON object. No markdown, no commentary, no code fences."""

JUDGE_USER_TEMPLATE = """\
Read this customer review and extract structured fields.

Field definitions:
- product: the primary product name mentioned. Extract as written, or your best plain
  description if no name is stated (e.g. "vacuum cleaner").
- stars: ONLY if the review explicitly states a numeric star/rating value (e.g. "4/5",
  "3 stars", "1 out of 5"). null if no explicit number is stated. Never infer this from
  tone alone.
- stars_inferred: your own holistic 1-5 estimate of the reviewer's satisfaction, based
  on the overall tone and content. Always populate this one.
- pros: list of distinct positive points mentioned, short phrases. Empty list if none.
- cons: list of distinct negative points, complaints, or defects mentioned, short
  phrases. Empty list if none.
- buy_again: true if the reviewer expresses intent to repurchase or recommends the
  product; false if they explicitly say they would NOT buy it again; null if not
  stated or genuinely ambiguous.
- sentiment: one of "positive", "negative", "neutral", "mixed". Use "mixed" only when
  the review has both a clearly positive and a clearly negative element.
- topics: short snake_case tags for the product aspects actually discussed (e.g.
  battery, build_quality, price, sound_quality).
- competitor_mentions: other brand or product names explicitly named. Empty list if
  none.
- urgency: how urgently a seller/support team should respond to this review.
  "high" = any physical harm or safety risk (pain, injury, fire, shock, hazard) no
    matter how the rest of the review reads, OR an explicit refund/return/replacement/
    legal demand, OR a repeated/systemic failure.
  "medium" = a concrete, fixable product or service defect, with no harm and no
    explicit escalation demand (e.g. "bluetooth keeps disconnecting").
  "low" = no concrete defect -- praise, neutral commentary, or a subjective preference
    only.
- feature_requests: explicit suggestions or wishes for product improvements. Empty
  list if none.
- language: one of "en" (English only), "hi-en" (Latin-script Hindi/English code-mix,
  a.k.a. Hinglish), "hi" (Hindi written in Devanagari script).

Review:
<review>
{text}
</review>

Return a JSON object with exactly these keys: product, stars, stars_inferred, pros,
cons, buy_again, sentiment, topics, competitor_mentions, urgency, feature_requests,
language."""


def build_user_prompt(text: str) -> str:
    """Return the judge user-prompt for a given review text."""
    return JUDGE_USER_TEMPLATE.format(text=text)


def parse_judge_response(raw: str) -> ReviewExtractionLLMOutput | None:
    """Parse and validate a judge's raw JSON response against the extraction schema.

    Returns None (rather than raising) on any parse/validation failure -- a judge that
    returns unparseable output is treated the same as a judge that errored: absent from
    that item's votes, not a crash.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        obj: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        return ReviewExtractionLLMOutput.model_validate(obj)
    except ValidationError:
        return None


def _extra_params_for(model_id: str) -> dict[str, Any]:
    for m in JUDGE_MODELS:
        if m["id"] == model_id:
            extra = m.get("extra_params")
            return dict(extra) if extra else {}
    return {}


async def call_judge(client: Any, model_id: str, text: str, timeout: int = 30) -> str:
    """Call one judge model with the review text; returns the raw response content string.

    Raises whatever the underlying Groq client raises on timeout/HTTP error -- callers
    are expected to catch and record per-model errors without crashing the whole run
    (see run_consensus.py), matching the existing multi_llm_labeler.py convention.

    `max_completion_tokens` is set generously (2000) for every model -- some models on
    this panel default to a hybrid "thinking" mode that can exhaust a smaller budget
    before ever emitting the requested JSON (see `qwen/qwen3.6-27b`'s `extra_params`
    comment in JUDGE_MODELS above). Per-model `extra_params` (e.g. `reasoning_effort`)
    are passed through when the model config declares them.
    """
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(text)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_completion_tokens=2000,
        timeout=timeout,
        extra_body=_extra_params_for(model_id) or None,
    )
    return response.choices[0].message.content or ""


def make_groq_client() -> Any:
    """Construct an AsyncGroq client using the dedicated benchmark key (never prod's)."""
    from groq import (
        AsyncGroq,  # noqa: PLC0415  -- deferred import, same pattern as multi_llm_labeler.py
    )

    key = load_benchmark_groq_key()
    return AsyncGroq(api_key=key)
