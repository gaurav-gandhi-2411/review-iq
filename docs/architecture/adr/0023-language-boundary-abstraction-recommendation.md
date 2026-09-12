# ADR 0023: Is discrete en/hi-en labeling the right abstraction? (Recommendation, not decision)

## Context

Session 11 P3d, following directly from ADR 0019 and ADR 0021's findings, which this ADR does
not re-derive, only synthesizes into a recommendation. ADR 0019 explicitly deferred this exact
question ("Recommend, don't decide: this is a definitional/product question for GG") without
answering it. This ADR answers it, as a recommendation GG can accept, reject, or amend — it
changes no code, no gate, no prompt.

**The evidence, from two independent methods, converges on the same conclusion:**

1. **Panel inter-rater agreement on `language` is 0.380** (Krippendorff's alpha, n=106, 3 LLM
   judges) — by far the lowest of any scored field (sentiment 0.942, buy_again 1.000, urgency
   0.796). It is low for every judge pair symmetrically (0.33–0.42), not a vendor-clustering
   artifact (ADR 0019).
2. **Production's own detector disagrees with the corpus's language tag on 55/106 (48.1%) of
   held-out fixtures** (ADR 0021) — measured by a completely different method (a deterministic
   heuristic detector vs. corpus-mining labels, not LLM-judge agreement).
3. This disagreement has a real, quantified cost when the binary label drives routing: **4.6pp,
   95% CI [2.8pp, 6.4pp]** of the measured accuracy gap is attributable purely to routing the
   wrong prompt, independent of prompt-quality contamination (7.8pp, separately measured).

Three independent methods — a corpus-mining heuristic, a production detector, and a three-judge
LLM panel — cannot agree on where "some Hindi words in an English review" becomes "a Hinglish
review." This is not a competence gap in any one component; it is evidence the boundary itself,
as currently specified (a single binary label), may not have a well-defined answer for a large
share of real Indian e-commerce reviews.

## Recommendation

**Do not attempt to sharpen the boundary — reduce how much correctness depends on it.**

The discrete `en`/`hi-en` label is currently doing two different jobs that have different
tolerance for ambiguity:

- **As a scoring/reporting category** (which language bucket a fixture's accuracy counts
  toward): tolerant of ambiguity by construction — ADR 0021's bootstrap CIs and the
  misrouting/contamination decomposition already absorb this noise honestly. No change needed
  here; this job is being done correctly today.
- **As a runtime routing signal** (which prompt variant `detect_language()` sends a review to):
  intolerant of ambiguity — a binary decision must be made per-request, and 48% of the time on
  this corpus, whatever gets decided will disagree with what a panel of independent judges would
  have said. This is the job actually costing 4.6pp.

**Given ADR 0019's own explicit warning against proposing a new threshold without evidence (the
exact mistake ADR 0014 already made once), the recommended next step is not a better boundary
definition — it is investigating whether the routing decision can be avoided rather than
improved.** Concretely: evaluate a single unified en+hi-en prompt (one prompt handling both,
carrying an explicit "the input may be English, Hinglish, or a code-mix of both; always output
English" instruction) as a follow-up experiment. If a unified prompt scores comparably to the
current per-language-routed pair, the discrete label becomes purely a reporting category — there
is no routing decision left to get wrong on the 48% of ambiguous cases, and the 4.6pp misrouting
cost disappears by construction rather than by drawing a sharper line that three independent
methods have already shown cannot be reliably drawn.

**This is a recommendation for a follow-up experiment, not a decision to build it.** It requires
new prompt-authoring and a new eval comparison (unified-prompt score vs. current routed score,
on the same held-out corpus) that is out of scope for this session.

## Alternatives considered

- **Define a graded/continuous code-mixing density score to replace the binary label.**
  Rejected as the immediate next step: this requires choosing a threshold or scale with no
  principled basis yet — the same evidence-free-threshold mistake ADR 0019 already flagged.
  Worth revisiting only if the unified-prompt experiment above fails to close the gap and a
  routing decision turns out to be unavoidable.
- **Keep the current per-language routing and accept the 4.6pp as a permanent cost.** Rejected
  as a recommendation (though it is the correct default if no one acts on this ADR): the cost is
  now precisely quantified and has a plausible zero-labeling-cost fix path (a prompt experiment,
  not new data collection) that hasn't been tried.
- **Have `detect_language()` itself return a confidence score and fall back to a "safe" default
  prompt below some confidence threshold.** Not recommended over the unified-prompt option: it
  still requires picking a threshold with the same evidence gap, and only mitigates the cost
  rather than removing the routing decision's failure mode entirely.
