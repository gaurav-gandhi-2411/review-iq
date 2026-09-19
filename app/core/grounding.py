"""Output-side grounding check for `competitor_mentions` -- Session 14 P4.

Neither the regex sanitize layer nor the model-based injection guard catches
field-targeted injection (eval/injection_suite.py's `field_targeted` family: 0/8
caught -- see eval/results/injection_suite_n40.json). Root cause, confirmed against
the real measured guard scores for those 8 cases (0.0004-0.0014, indistinguishable
from benign): `meta-llama/llama-prompt-guard-2-86m` is trained to detect
instruction-hijacking/jailbreak framing ("ignore previous instructions," role-play),
not a narrow, plausible-sounding field-override instruction embedded in otherwise
normal review text (see app/core/injection_guard.py's own documented limit).

This is a different, complementary control: instead of trying to detect the
injection attempt in the INPUT, it checks whether the OUTPUT is actually grounded
in the source text, for the one field where that's a meaningful, low-false-positive
check -- `competitor_mentions`. The schema defines this field as brand/product names
"explicitly named" in the review (see JUDGE_USER_TEMPLATE in
eval/consensus/panel.py for the same field definition used independently), so a
real one should appear verbatim (case-insensitive) somewhere in the source text; a
fabricated one won't.

Deliberately NOT applied to pros/cons/topics/feature_requests: the schema documents
those as "short phrases" -- i.e. paraphrases by design. A substring check there
would false-positive on legitimate paraphrased output (verified: see
eval/measure_grounding_check.py's false-positive measurement against the 106-item
held-out corpus, run before this was scoped down to competitor_mentions only).

Also NOT applied to buy_again/sentiment/stars_inferred/urgency: these are the
model's own judgment calls, not extractions -- "does the value appear in the text"
is not a meaningful question for them. This is exactly why 4 of the 8
field-targeted attacks (f4-01 buy_again, f4-02 sentiment, f4-03 stars_inferred,
f4-04 urgency) cannot be closed by any output-grounding check, and 2 more (f4-05
topics, f4-06 cons forced empty) are suppression, not fabrication -- there is no
forged value for a grounding check to catch. Only f4-07 (a fabricated competitor
name) and f4-08 (a fabricated pros phrase, not covered by this narrower scope) are
even the right shape for this control.

IMPORTANT further limit, found while writing this module's own test (it caught a
wrong assumption in an earlier draft, see tests/unit/test_grounding.py): f4-07's
attack text spells its target brand name out directly inside the injection
instruction itself ("...always include 'Samsung'..."). A substring check against
the raw input necessarily finds it "in the text" -- the attacker put it there. This
control cannot distinguish "the reviewer genuinely named this brand" from "the
attacker's own instruction payload named it," and does NOT close f4-07 as
literally worded in the suite today. What it DOES close is the more common real
shape: a competitor name the model outputs that appears nowhere in the input at
all (a hallucination, or an injection phrased without spelling out its target
value in the payload). See SECURITY.md for the scoped public claim -- this is a
real, narrower-than-first-described control, not a full field-targeted-injection
defense.
"""

from __future__ import annotations


def ungrounded_competitor_mentions(review_text: str, competitor_mentions: list[str]) -> list[str]:
    """Return the subset of `competitor_mentions` that do NOT appear (case-insensitive
    substring match) anywhere in `review_text`.

    An empty return means every named competitor is grounded in the source text. A
    non-empty return is the set of values a caller should not trust as-is -- either a
    genuine model hallucination or a successful field-targeted injection; this function
    doesn't distinguish the two causes, only that the value has no textual basis.
    """
    haystack = review_text.lower()
    return [name for name in competitor_mentions if name.lower() not in haystack]
