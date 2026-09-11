"""LLM judge panel for eval-fixture consensus ground truth -- multi-vendor, no prod keys.

History: originally 3 Groq-hosted candidates; Session 8 P2 removed openai/gpt-oss-120b
(a self-judging conflict -- it was review-iq's own production model) leaving exactly
one calibration-passing, disjoint judge (qwen/qwen3.6-27b) -- no inter-rater kappa/alpha
computable with one rater. Session 8 P3 added qwen/qwen3.8-27b (same vendor, a
different checkpoint) as a partial fix. Session 9 P3a completes it with a genuinely
cross-vendor third judge, restoring real multi-vendor agreement.

Current panel (4 candidates, 3 calibration-passing -- see calibration_report.json):

  1. qwen/qwen3.6-27b       (Groq, owned_by: Alibaba Cloud)
  2. qwen/qwen3.8-27b       (Groq, owned_by: Alibaba Cloud) -- same vendor as #1
  3. gemini-3.5-flash-lite  (Google Gemini API, owned_by: Google) -- genuinely cross-vendor
  4. allam-2-7b             (Groq, owned_by: SDAIA) -- FAILS calibration, dropped

Why NOT `llama-3.3-70b-versatile` / `openai/gpt-oss-120b`: both were, in turn, review-iq's
own production tiered-router large-tier extraction model at different points in this
repo's history -- using either to judge extraction quality would be the model judging
itself. See assert_no_self_judging() below and docs/architecture/adr/0013-*.md for the
gpt-oss-120b incident this check exists to prevent recurring under a third name.

Gemini (`GEMINI_API_KEY`), Session 9 P3a: this key is wired into production as the
SecondaryProvider failover model (`app/core/llm.py::_call_gemini`), which raised the
same self-judging-adjacent concern Groq's prod key raised on 2026-07-07 (see
`benchmark_groq_key.py`'s docstring) -- using a key that also serves real traffic risks
a quota/traffic collision, or evaluating against a model that could itself become part
of the serving path. VERIFIED DIRECTLY before using it (2026-09-11, `gcloud run
services describe`): `ENABLE_GEMINI_FALLBACK` is NOT set in production's environment,
so the fallback path is at its code default of `False` -- genuinely dormant, not merely
low-traffic. This is a point-in-time fact, not a permanent guarantee: if GG ever
enables the fallback, gemini-*'s judge status must be re-examined the same way
gpt-oss-120b's was, not left on the assumption this one check made once. No dedicated
"benchmark-only" Gemini key exists (unlike Groq's); using the same key production would
use if enabled is accepted here because that path is currently proven off, not because
the distinction stopped mattering.

Two real dead ends on the way to gemini-3.5-flash-lite, found and root-caused (not
assumed) before it worked: `gemini-2.5-flash` calibrated at 9/33 misses in a suspicious
pattern (early items clean, later items failing near-total) -- direct inspection of one
failing call's actual exception found a **20-requests-per-DAY** free-tier cap for that
specific model (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), not the 5/minute
limit `_call_gemini_judge`'s retry-on-429 already handles; a 16-item calibration run
alone exhausts more than half that daily budget, and any real labeling volume would
exhaust it immediately. `gemini-2.5-flash-lite` calibrated at 33/33 misses (every field,
every item) -- direct inspection found the true cause was an HTTP 404, not a judgment
failure: that model is "no longer available to new users" (the API's own error message
recommends `gemini-3.5-flash-lite`, which is what JUDGE_MODELS uses now).

Residual risk on ALLaM-2-7B, documented rather than hidden: its own technical materials
describe it as initialized from a Llama-2 checkpoint with extensive continued
pretraining on Arabic+English corpora by SDAIA (a different organization, different
corpus emphasis, different base version -- Llama-2, not the Llama-3.3-70b-versatile
this repo used in prod at the time this was written). This is NOT "zero shared
ancestry" with Meta Llama, but it is categorically different from the self-judging
conflict flagged above. Moot in practice: it fails calibration on real capability
grounds (see CALIBRATION OUTCOME) regardless of the lineage question.

CALIBRATION OUTCOME (eval/consensus/results/calibration_report.json has the full data):
`allam-2-7b` FAILED calibration reproducibly across two independent runs (9/33
control-set field checks wrong both times -- same items, same fields: missed the
mixed-sentiment case cal-012, omitted the `language` key entirely on the Hindi case
cal-004, missed the explicit-refund-demand urgency=high case cal-013's sibling cal-007,
among others) and was DROPPED from the active panel, per the explicit "do not silently
keep a failing judge" instruction. `qwen/qwen3.6-27b` initially also failed (10/33
misses) but that traced to a real call-configuration bug (Groq's hybrid "thinking" mode
exhausting the completion-token budget before emitting JSON), fixed via
`reasoning_effort="none"`, not a judgment problem.

Net result: the ACTIVE PANEL is 3 judges (`qwen/qwen3.6-27b`, `qwen/qwen3.8-27b`,
`gemini-3.5-flash-lite`), a genuine improvement over Session 8's 2-same-vendor-judge
panel -- real 3-way (and per-2-judge-subset) Krippendorff's alpha / Fleiss' kappa are
now computable, see docs/architecture/adr/0016-*.md for the numbers and what they mean
for label trust.

Groq judges are called via the dedicated benchmark key (`benchmark_groq_key.py`), never
`GROQ_API_KEY` (prod). The Gemini judge uses `GEMINI_API_KEY` directly (see above for
why that's currently acceptable). No model sees another's answer -- independent
concurrent calls, no shared conversation context.
"""

from __future__ import annotations

import asyncio
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
    # Session 9 P3a: a THIRD judge, genuinely cross-vendor (Google, not Alibaba) -- the
    # qwen3.6/qwen3.8 pair above shares one vendor, so their agreement alone overstates
    # independence (same caution class as the gpt-oss-120b contamination, smaller in
    # degree). Uses the standalone Gemini call path this repo already built for exactly
    # this "no live Groq call" constraint (scripts/record_cassettes_via_fallback.py's
    # _call_gemini_raw, same client construction/JSON-mode config/temperature=0.0).
    #
    # Self-judging check, done manually since assert_no_self_judging() only compares
    # against groq_model_small/large: this is review-iq's `gemini_model` config value,
    # used ONLY as SecondaryProvider failover (app/core/llm.py::_call_gemini), gated by
    # `ENABLE_GEMINI_FALLBACK`. Verified directly against live production config
    # (2026-09-11, `gcloud run services describe`): ENABLE_GEMINI_FALLBACK is NOT set in
    # production's environment, so it is at its code default of False -- the fallback
    # path is genuinely dormant, not just theoretically low-traffic. This is a
    # point-in-time fact, not a permanent guarantee: if GG ever enables the fallback,
    # this judge becomes a real self-judging conflict the same way gpt-oss-120b was and
    # must be re-excluded then, not left on the assumption this check made once.
    {
        "id": "gemini-3.5-flash-lite",
        "provider": "gemini",
        "family": "Google Gemini",
        "owner": "Google",
        # NOT review-iq's configured gemini_model default ("gemini-2.0-flash") --
        # verified live (2026-09-11, listing the real Gemini API catalog with this
        # exact key): gemini-2.0-flash no longer exists in the current model list,
        # confirming Session 7's independent finding that Google deprecated it
        # 2026-06-01. gemini-3.5-flash-lite is the current stable (non-preview) flash-tier
        # model. Since production's Gemini fallback is verified disabled (see above),
        # this judge does not need to match whatever review-iq would call in
        # production even if it did fire -- it only needs to be a real, working,
        # cross-vendor model.
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


def _provider_for(model_id: str) -> str:
    for m in JUDGE_MODELS:
        if m["id"] == model_id:
            return m["provider"]
    return "groq"


# Session 9 P3a incident: the first calibration attempt against gemini-3.5-flash-lite
# scored 16/33 misses -- a suspicious pattern (every item after the first ~6 missed
# almost every field). Direct inspection of one failing item's actual exception (not
# assumed) found the true cause: Gemini's free tier caps gemini-3.5-flash-lite at 5
# requests/MINUTE per project (google.genai.errors.ClientError 429
# RESOURCE_EXHAUSTED, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier ... limit:
# 5"), far tighter than Groq's TPD-shaped limits this codebase's existing retry/pacing
# logic was built around. Every call past the first ~5 in a tight loop was silently
# swallowed by call_judge's caller (a 429 registers as "parse failure" -> a miss on
# every field, not a fixable judgment error) -- a call-configuration bug, not evidence
# the model can't judge, the same class as qwen3.6-27b's hybrid-thinking-mode miss
# before its reasoning_effort fix. Retrying with backoff on 429 specifically (not a
# blanket retry-everything, which would mask a genuine content/parse problem as a
# transient one) is the fix -- honoring the server's own `retryDelay` naturally paces
# subsequent calls to Gemini's real throughput ceiling, without a separate proactive
# delay mechanism duplicating what the 429 response already tells us to do.
_GEMINI_MAX_RETRIES = 3


async def _call_gemini_judge(model_id: str, text: str, timeout: int = 30) -> str:
    """Call a Gemini judge; returns the raw response text.

    Mirrors scripts/record_cassettes_via_fallback.py::_call_gemini_raw exactly (same
    client construction, same JSON-mode config, same temperature=0.0) -- that function
    was already built and verified for this repo's "no live Groq call" constraint;
    this reuses the identical pattern for the analogous "no live prod-key call for
    unrelated traffic" concern (see JUDGE_MODELS' gemini-3.5-flash-lite entry for the
    verification that this key's production fallback path is currently dormant).

    Reads GEMINI_API_KEY directly from the environment rather than via
    app.core.config.get_settings() -- deliberately not coupled to the full Settings
    object, which requires many unrelated production env vars to construct.

    Retries on 429 (rate limit) specifically, honoring the server's own `retryDelay`
    when present -- see the incident note above _GEMINI_MAX_RETRIES for why this
    exists. Any other error propagates immediately, unretried.
    """
    import os
    import re

    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set -- required for the gemini-3.5-flash-lite judge."
        )
    client = genai.Client(api_key=api_key)

    last_exc: Exception | None = None
    for attempt in range(_GEMINI_MAX_RETRIES):
        try:
            return await _generate_gemini_content(client, model_id, text, timeout, types)
        except genai_errors.ClientError as exc:
            if getattr(exc, "code", None) != 429 or attempt == _GEMINI_MAX_RETRIES - 1:
                raise
            last_exc = exc
            match = re.search(r"'retryDelay': '(\d+)", str(exc))
            delay = float(match.group(1)) + 1.0 if match else 20.0
            await asyncio.sleep(delay)
    raise last_exc or RuntimeError("unreachable")


async def _generate_gemini_content(
    client: Any, model_id: str, text: str, timeout: int, types: Any
) -> str:
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=model_id,
            contents=build_user_prompt(text),
            config=types.GenerateContentConfig(
                system_instruction=JUDGE_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            ),
        ),
        timeout=timeout,
    )
    return response.text or ""


async def call_judge(client: Any, model_id: str, text: str, timeout: int = 30) -> str:
    """Call one judge model with the review text; returns the raw response content string.

    Raises whatever the underlying client raises on timeout/HTTP error -- callers are
    expected to catch and record per-model errors without crashing the whole run (see
    run_consensus.py), matching the existing multi_llm_labeler.py convention.

    `max_completion_tokens` is set generously (2000) for every Groq model -- some
    models on this panel default to a hybrid "thinking" mode that can exhaust a
    smaller budget before ever emitting the requested JSON (see `qwen/qwen3.6-27b`'s
    `extra_params` comment in JUDGE_MODELS above). Per-model `extra_params` (e.g.
    `reasoning_effort`) are passed through when the model config declares them.

    Gemini judges bypass `client` entirely (a Gemini client is not interchangeable
    with a Groq client) -- see _call_gemini_judge().
    """
    if _provider_for(model_id) == "gemini":
        return await _call_gemini_judge(model_id, text, timeout=timeout)
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
