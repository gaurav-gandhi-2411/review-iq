"""3-judge LLM panel for eval-fixture consensus ground truth -- Groq, dedicated benchmark key.

Panel composition (queried live via Groq's `/v1/models` endpoint -- `owned_by` field --
the same technique `benchmark/vernacular_v2/multi_llm_labeler.py` already uses to confirm
family/owner rather than assuming from naming):

  1. openai/gpt-oss-120b  (owned_by: OpenAI)  -- OpenAI GPT-OSS family
  2. qwen/qwen3.6-27b     (owned_by: Alibaba Cloud) -- Alibaba Qwen family
  3. allam-2-7b           (owned_by: SDAIA)   -- Saudi Data & AI Authority's ALLaM family

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
    {
        "id": "openai/gpt-oss-120b",
        "provider": "groq",
        "family": "OpenAI GPT-OSS",
        "owner": "OpenAI",
    },
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
