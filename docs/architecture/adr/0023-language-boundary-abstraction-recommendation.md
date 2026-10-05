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


## Correction (Session 15d)

Dated 2026-09-20. The sections above are left as written. Evidence:
`docs/specs/s15c-language-routing.md` (S2), `scripts/measure_routing_cost.py`,
`eval/results/routing_cost_n106.json` (git_sha `c4dcfcd`, sha256 `5fbca0a8...`), re-derived from
recorded predictions, zero live calls. GG decision D7 adopted the S2 recommendation. See also the
Correction section in ADR 0021, which carries the full derivation.

**The 4.6pp cost cited in Context item 3 is circular.** In the forced-routing condition the prompt
says `language: always "hi-en"`, so the `language` field scores 100% by echo; as deployed it equals
the detector's agreement with the corpus label. Over all 10 fields that field supplies 111% of the
+4.68pp [+2.87, +6.62] difference. Excluding `stars` (constant) and `language`, forcing the correct
routing moves the held-out headline by **-0.63pp [-2.77, +1.51]** (72.40% [69.78, 74.94] as
deployed vs 71.76% [69.36, 74.11] forced): no measurable extraction cost of misrouting.

**Conclusions that no longer stand:**

- Context item 3 and every statement that the routing decision "is the job actually costing 4.6pp".
- "the 4.6pp misrouting cost disappears by construction" if routing is removed (Recommendation), and
  "accept the 4.6pp as a permanent cost ... the cost is now precisely quantified" (Alternatives).
  There is no measured extraction cost to remove. The unified-prompt experiment is therefore no
  longer motivated by a 4.6pp recovery; if run at all it is a simplification test (can the router
  be deleted without loss), staged and budgeted in `docs/specs/s15c-language-routing.md`
  (S2c, and its Stage 1 design section), not a cost-recovery test.
- Reading the 48.1% disagreement as detector error. It is agreement with the corpus label
  (95% CI 38.8-57.5) and that label has alpha 0.380 (Context item 1), so the figure partly measures
  label noise. Never call it accuracy.

**What stands:**

- Context items 1 and 2 as measurements: alpha 0.380 on `language` (quoted from ADR 0019, not
  recomputed) and 55/106 detector-vs-label disagreement. Together they still show the en/hi-en
  boundary is not well defined for many reviews.
- The recommendation not to sharpen the boundary or invest in a better detector: strengthened,
  because sharpening it now has no measurable extraction benefit either (D7: no detector
  investment).
- The "graded code-mixing score" alternative: no longer needs to wait for the unified-prompt
  experiment. It shipped in a deliberately minimal form as additive response fields
  (`code_mixed`, `language_signal_strength`) that report the detector's own rule-hit evidence; the
  strength is an ordinal heuristic, not a probability, and is not calibrated. Routing is unchanged.
- The classification of the label's two jobs: as a reporting category it is now handled by
  excluding `language` from the headline and reporting agreement (never accuracy) beside its alpha.
