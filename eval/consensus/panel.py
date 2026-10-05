"""LLM judge panel for eval-fixture consensus ground truth -- multi-vendor, no prod keys.

History: originally 3 Groq-hosted candidates; Session 8 P2 removed openai/gpt-oss-120b
(a self-judging conflict -- it was review-iq's own production model) leaving exactly
one calibration-passing, disjoint judge (qwen/qwen3.6-27b) -- no inter-rater kappa/alpha
computable with one rater. Session 8 P3 added qwen/qwen3.8-27b (same vendor, a
different checkpoint) as a partial fix. Session 9 P3a completes it with a genuinely
cross-vendor third judge, restoring real multi-vendor agreement.

S17 UPDATE (ADR 0034/0035): the Gemini fallback was retired from production and
`gemini-3.5-flash-lite` was dropped from JUDGE_MODELS (recorded in RETIRED_JUDGE_MODELS; its
call code is gone). A fresh panel-1 run now uses qwen3.6-27b + qwen3.8-27b (+ allam, which fails
calibration), and passes assert_no_self_judging. The existing panel-1 labels (the 106 held-out
fixtures, consensus_labels.jsonl, calibration_report.json) were produced with the OLD
three-judge roster below and are unchanged; everything from here to the CALIBRATION OUTCOME
paragraph is the record of that old roster, not the current one.

Roster at the time of the original labeling run (4 candidates, 3 calibration-passing -- see
calibration_report.json):

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


# Retired from the panel-1 roster in S17 (ADR 0034/0035). Kept as DATA ONLY so the existing panel-1
# labels (eval/consensus/results/*, the 106 held-out fixtures) stay interpretable: they were
# produced by qwen3.6-27b + qwen3.8-27b + this judge. It has no call path any more (the Gemini
# client code was removed with the production fallback) and must not be added back to JUDGE_MODELS
# while any Google model is a production path -- the self-judging guard would reject it.
RETIRED_JUDGE_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "gemini-3.5-flash-lite",
        "provider": "gemini",
        "family": "Google Gemini",
        "owner": "Google",
        "retired_reason": (
            "shared a vendor with review-iq's Gemini fallback (retired S17); panel-1 labels "
            "produced with this judge are historical and unchanged"
        ),
    },
)


# Models review-iq is documented to fail over to even when the env var is unset locally:
# docs recommend SECONDARY_PROVIDER_MODEL=meta-llama/llama-3.3-70b-instruct (app/core/config.py).
# Always checked so a local environment without that env var cannot silently weaken the guard.
KNOWN_FAILOVER_MODELS: tuple[str, ...] = ("meta-llama/llama-3.3-70b-instruct",)

# Vendor family by the organisation prefix of an OpenRouter/Groq style id ("org/model").
_ORG_FAMILY: dict[str, str] = {
    "openai": "openai",
    "meta-llama": "meta",
    "meta": "meta",
    "google": "google",
    "qwen": "alibaba",
    "alibaba": "alibaba",
    "deepseek": "deepseek",
    "deepseek-ai": "deepseek",
    "nvidia": "nvidia",
    "mistralai": "mistral",
    "z-ai": "zhipu",
    "zhipu": "zhipu",
    "thinkingmachines": "thinkingmachines",
    "anthropic": "anthropic",
    "x-ai": "xai",
    "moonshotai": "moonshot",
    "microsoft": "microsoft",
    "cohere": "cohere",
    "allenai": "allenai",
    "minimax": "minimax",
}
# Vendor family for bare ids with no organisation prefix (Gemini API, Groq legacy ids).
_ID_PREFIX_FAMILY: tuple[tuple[str, str], ...] = (
    ("gemini", "google"),
    ("gemma", "google"),
    ("llama", "meta"),
    ("gpt-", "openai"),
    ("allam", "sdaia"),
    ("qwen", "alibaba"),
    ("mistral", "mistral"),
    ("deepseek", "deepseek"),
)


def model_family(model_id: str) -> str:
    """Vendor family of a model id; raises ValueError (fail closed) if not in the mapping."""
    mid = model_id.strip().lower()
    if "/" in mid:
        org = mid.split("/", 1)[0]
        if org in _ORG_FAMILY:
            return _ORG_FAMILY[org]
    else:
        for prefix, family in _ID_PREFIX_FAMILY:
            if mid.startswith(prefix):
                return family
    raise ValueError(
        f"Unknown vendor family for model id {model_id!r}: add its organisation to _ORG_FAMILY "
        "(or its bare-id prefix to _ID_PREFIX_FAMILY) in eval/consensus/panel.py. Failing "
        "closed: an unmapped model could be a production vendor under a name this guard "
        "does not know."
    )


def production_model_ids() -> dict[str, str]:
    """{source: model_id} for every model review-iq can serve from, read at runtime.

    Data-driven: every non-empty string Settings field whose name contains "model" is a
    production model id (groq_model, groq_model_small/large, secondary_provider_model, and any
    future provider added the same way), so re-adding a provider (say a Gemini fallback) puts its
    vendor back in the forbidden set without anyone editing this guard. Plus KNOWN_FAILOVER_MODELS.
    """
    from app.core.config import get_settings

    settings = get_settings()
    names = list(getattr(type(settings), "model_fields", None) or dir(settings))
    ids: dict[str, str] = {}
    for name in names:
        if name.startswith("_") or "model" not in name or name == "model_fields":
            continue
        value = getattr(settings, name, "")
        if isinstance(value, str) and value:
            ids[name] = value
    for i, mid in enumerate(KNOWN_FAILOVER_MODELS):
        ids[f"known_failover_model_{i}"] = mid
    return ids


def assert_no_self_judging(
    judge_models: tuple[dict[str, str], ...] = JUDGE_MODELS,
    *,
    extra_forbidden_families: dict[str, str] | None = None,
) -> None:
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

    S17 G6a hardening: the original check compared judge ids to the two Groq production ids
    EXACTLY, which missed (a) the OpenRouter failover model (Llama) and the dormant Gemini
    fallback model, and (b) any sibling checkpoint of a production vendor (a judge
    `llama-3.1-8b-instruct` is not the string `meta-llama/llama-3.3-70b-instruct` but is the
    same vendor family as the failover). It now reads ALL production model ids at runtime
    (groq small/large, secondary provider model, Gemini fallback model, plus
    KNOWN_FAILOVER_MODELS) and compares VENDOR FAMILY via `model_family()`. A model whose
    family is not in the explicit mapping fails closed (add it to _ORG_FAMILY/_ID_PREFIX_FAMILY).
    History: when this hardening landed it flagged panel 1's `gemini-3.5-flash-lite` (Google =
    the vendor of the Gemini fallback). S17 retired the fallback and dropped that judge from
    JUDGE_MODELS, so the real roster passes again; the Google rule is now only as strong as the
    Settings-derived list (see production_model_ids).
    """
    # `extra_forbidden_families` ({family: reason}) lets a runner add families beyond
    # production (panel 2 forbids the panel-1 vendors for independence).
    forbidden: dict[str, str] = {}
    for source, mid in production_model_ids().items():
        forbidden.setdefault(model_family(mid), f"production {source}={mid}")
    for fam, reason in (extra_forbidden_families or {}).items():
        forbidden.setdefault(fam, reason)

    conflicts: list[str] = []
    for m in judge_models:
        fam = model_family(m["id"])  # raises (fail closed) on an unknown family
        if fam in forbidden:
            conflicts.append(f"{m['id']} (family {fam!r}; conflicts with {forbidden[fam]})")
    if conflicts:
        raise ValueError(
            f"Self-judging conflict: judge candidate(s) {conflicts}. A judge must never be the "
            "same model, or the same vendor family, as a model the extraction pipeline under "
            "test can run (Groq tiers, OpenRouter failover, any other configured provider) -- remove the "
            "conflicting candidate(s) from the roster before running any consensus labeling. "
            "See this function's docstring and docs/architecture/adr/0013-*.md."
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

    Raises whatever the underlying client raises on timeout/HTTP error -- callers are
    expected to catch and record per-model errors without crashing the whole run (see
    run_consensus.py), matching the existing multi_llm_labeler.py convention.

    `max_completion_tokens` was originally 2000 ("generously", for models that default to a
    hybrid "thinking" mode that can exhaust a smaller budget before ever emitting the
    requested JSON -- see `qwen/qwen3.6-27b`'s `extra_params` comment in JUDGE_MODELS above).
    Session 10 P5e found live evidence this was too generous in the other direction: Groq
    enforces a separate, per-model Output Tokens Per Minute (OTPM) admission-control check --
    "Request too large ... on output tokens per minute (OTPM): Limit 1000, Requested 1387" --
    that rejects the call BEFORE it runs based on the requested `max_completion_tokens`, not
    actual usage. This is a different limit from the general token-bucket headroom ADR 0015's
    Session 10 follow-up measured (which stayed healthy throughout); 2000 tripped this
    admission check on nearly every call during a real batch, silently degrading panel size
    (a judge that 429s becomes NO_RESPONSE, and consensus among the survivors can still read
    "unanimous" with only 1-2 of 3 judges actually present -- see the fixtures discarded and
    relabeled because of this). 900 is verified (live) to clear the OTPM check for this org/
    model combination with real headroom to spare; `reasoning_effort="none"` already disables
    the thinking-mode overhead 2000 was originally sized for, so this is not expected to
    truncate real responses. Per-model `extra_params` (e.g. `reasoning_effort`) are passed
    through when the model config declares them.

    """
    response = await client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(text)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_completion_tokens=900,
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
