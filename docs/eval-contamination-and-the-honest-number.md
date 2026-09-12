# The honest number: measuring — and finding — contamination in this project's own eval

**Status: DRAFT. Not yet published to any public surface. GG reviews before this goes live
anywhere (README, blog, site).**

## TL;DR

This product's published accuracy numbers were, for most of this project's history, measured
against text the prompts were tuned against — a form of contamination as real and as easy to
fall into as training-test leakage in any ML system, just less discussed for prompt-engineered
LLM products. We built a held-out corpus specifically to measure without that contamination,
and the gap was real: **12.3 percentage points, 95% CI [6.5pp, 17.4pp], n=106** (68.3% held-out
vs. 80.6% published, before this session's fixes). We then decomposed that gap into two
independently-measurable causes — a language-routing bug (4.6pp) and genuine prompt-quality
contamination (7.8pp) — and made the measurement itself reproducible from committed cassettes,
so this number, unlike every one before it, can be independently re-derived rather than taken
on faith.

Along the way, the process that produced this number failed in several specific, documented
ways. Those failures are covered below in as much detail as the successes — deliberately: **a
project that only reports its clean results and hides its false starts is asking to be trusted
on faith, exactly the failure mode this whole exercise exists to avoid.**

## The mechanism: how contamination gets in

Every fixture in this project's original 49-fixture eval set was either hand-authored by the
same process that iterated on the extraction prompts, or labeled by a single model with no
adversarial or independent check. When you tune a prompt against a fixture set and then score
that same prompt against that same fixture set, the resulting accuracy number measures how well
the prompt was tuned to those specific examples — not how well it generalizes to review text no
one involved in prompt development has ever seen. This is the same leakage failure mode as
training and testing on overlapping data in classical ML; it is just easier to fall into
unnoticed with prompts, because there is no train/test split ceremony forcing the question.

There was a second, independent source of the same problem, found only while investigating the
first: **neither of the two functions behind every published number
(`eval/runner.py::run_single`/`run_single_routed`) ever calls the production language detector.**
Both force-feed each fixture's own recorded ground-truth language straight into the prompt
builder. Real traffic (`app/api/demo.py`, `app/api/v2/extract.py`) calls `detect_language()`
first and routes on whatever it returns — right or wrong. Every published number this product
has ever reported was measured with language routing assumed correct, never as it actually
happens for a real customer. This is a routing bug wearing the same disguise as prompt
contamination: both make the CI-gate number look better than a real customer's experience,
for entirely different underlying reasons.

## Building a corpus the prompt has never seen

The fix is the standard one from ML: hold out a set of examples that never influences prompt
development, and score against it separately. Concretely:

- **Source**: 14,552 unique reviews mined from three public Kaggle Flipkart datasets
  (`eval/data/flipkart_candidates.jsonl`) — real Indian e-commerce review text, not synthetic.
- **Yield**: 106 genuine Hinglish (Roman-script code-mixed) candidates (0.74% of the corpus).
  Devanagari-script Hindi yield is **zero** — not low, zero, confirmed after fixing a detector
  bug that had been counting stray Devanagari *punctuation* (the danda, `।`, used as a period in
  otherwise-English text) as Hindi *content*. Three independent research passes across this
  project's history converged on the same real-world distribution: Indian e-commerce reviews in
  this domain are written in English or romanized Hinglish, essentially never in Devanagari
  script. (This finding is why Devanagari Hindi support has since been retired from this
  product's claims entirely — see ADR 0022 — rather than kept as a permanently "experimental"
  label for a bucket that has now been shown three times over that it cannot grow.)
- **Quarantine discipline**: candidates go into `eval/fixtures/_held_out_hindi_hinglish/`, a
  directory the CI-gate eval runner never walks — an underscore-prefix naming convention that is
  a structural exclusion (the runner's fixture-collection code literally cannot see it), not a
  policy someone has to remember to follow. The same mechanism now also protects the retired
  synthetic Hindi fixtures (`eval/fixtures/_quarantine_synthetic_hi/`) from silently re-entering
  the scored set.
- **Labeling**: a 3-judge LLM consensus panel (below), not the same process that iterates on
  prompts, and not a single model's opinion.

## Consensus methodology, and what went wrong with it

Ground truth for the held-out corpus comes from a panel of independent LLM judges voting on
each field, with a "silver" label taken when the panel reaches unanimous or majority agreement.
This is a real improvement over single-model labeling — but "independent" and "reaches
agreement" both turned out to have failure modes that only surfaced under scrutiny, and each
one is documented here rather than quietly fixed and forgotten.

### 1. The panel was judging itself

A judge model is only independent evidence if it isn't, in effect, checking its own work. This
project's panel initially included `openai/gpt-oss-120b` — which, after an unrelated production
model migration, had silently become this product's own `groq_model_large`. The panel was
checking the production model's answers using the production model. Measured effect: the
contaminated judge's vote matched the production model's exact hedge in **9 of 9** real
disagreements it was asked to adjudicate — a rate far more consistent with "same model, same
reasoning on the same text" than genuine independent judgment.

The fix was a runtime check (`panel.assert_no_self_judging()`), not just a docstring warning —
the docstring had, in fact, correctly documented the rule before the migration; it was simply
never re-checked against the new production model name. Re-running the analysis with only the
one genuinely disjoint judge in the panel produced numbers that moved in **both** directions
from what contamination alone would predict: one field's true recoverable headroom turned out to
have been *understated*, not inflated, by the contaminated result. This is why fixing an
independence bug isn't automatically going to make your numbers look worse — it makes them
*correct*, whichever direction that turns out to be.

### 2. A "confirming" judge was actually a degenerate hedger

Adding a third judge (Gemini) initially looked like it retracted the self-judging finding above
— it agreed with the production model's hedge on 9/9 hard sentiment cases, which read as
independent corroboration that those cases really were ambiguous. That reading was never
actually tested against the alternative explanation until a targeted control was built: is this
judge hedging because the text is genuinely ambiguous, or because the judge hedges specifically
*when a case is hard*, independent of whether it's actually decidable?

A calibration set built from unambiguous, easy items cannot distinguish these two explanations
by construction — it contains no hard items to fail on. So a **hard-but-decidable control** was
built instead: fixtures where a disjoint, calibration-passing judge already agrees with the
original ground truth, but the production model itself gets wrong or hedges on. Against this
control, the "confirming" judge hedged on 4 of 5 items — it wasn't finding genuine ambiguity, it
was defaulting to "mixed" whenever the text carried real directional signal (sarcasm,
frustration, an explicit competitor comparison), and getting it right only on the one item that
carried no such signal. **The generalizable lesson: calibrating a judge on an unambiguous-only
control set cannot catch a judge that specifically fails on hard items — any future judge
vetting needs a hard-but-decidable arm, not just an easy one, or this exact failure mode repeats
under a different judge's name.**

### 3. The panel could report "unanimous" without actually achieving it

Separately from judge quality: the voting logic that turns individual judge votes into a
"unanimous"/"majority"/"split" agreement label had a scope bug. When a judge legitimately failed
to respond (a real API error, not a null answer), that judge was silently dropped from the
*denominator* used to decide unanimity — so if the two remaining judges happened to agree, the
record was labeled "unanimous" even though a third invited judge never actually weighed in.
Roughly 40 fixtures in this project's history carried this false "unanimous" label at some
point. The fix changes the denominator from "judges who responded" to "judges who were invited,"
across all three voting functions, with a regression test that directly exercises the
previously-broken shape (a NO_RESPONSE judge present, full agreement among the rest) and asserts
the result is never mislabeled "unanimous." A full mechanical audit of every consensus record
currently committed to this repo found **zero** remaining instances of the bug in the live,
deduplicated data — the false-unanimous records that did exist were all from batches that had
already been discarded or relabeled for unrelated reasons before this fix landed, not from data
this product has ever actually published a number from.

### 4. [Flagged for GG, not written up]

The brief for this document asked for a fourth entry here: a case where a check was hardcoded to
report PASS for an extended period (stated as "42 days") rather than genuinely evaluating
anything. **This entry could not be verified against this repository's actual history** —
extensive search across every ADR (0001–0023), the full commit log, and CI workflow history
found no incident matching that description. Rather than write a plausible-sounding but
unverified story into a document whose entire purpose is auditable honesty, this section is left
as an open question: **GG, if this refers to a specific incident, please point to it (a commit,
an ADR, or which system) and it will be added with the same level of verified detail as the
three above; otherwise this entry should be dropped before publication.**

## The measured gap, with confidence intervals

All numbers below are reproducible from committed cassettes — `EVAL_CASSETTE_MODE=replay`, zero
live API calls, byte-identical output verified by direct diff against a from-scratch replay.
This is itself a fix: the original 67.9% figure (n=23) was measured against the live production
endpoint over HTTP with no cassette, and could not be independently re-derived by anyone who
didn't have live Groq API access at that exact moment. It has since been fully superseded by a
methodology that calls the same underlying extraction function in-process, under the same
cassette mechanism the CI-gate eval already uses, pointed at its own separate cassette store.

| Measurement | Score | 95% CI | n |
|---|---|---|---|
| Held-out, as actually deployed (real language routing) | **68.3%** | [66.3%, 70.3%] | 106 |
| Held-out, language routing forced correct | 72.9% | [71.1%, 74.6%] | 106 |
| Currently published hi-en (fixtures the prompt was tuned against) | 80.6% | — | 15 |

**The contamination gap decomposes into two separately-measured, additive effects, not one
blended number:**

- **Language misrouting cost: 4.6 percentage points, 95% CI [2.8pp, 6.4pp]** (paired bootstrap,
  same 106 items scored under both routing conditions) — what the production language detector
  getting the wrong prompt roughly half the time actually costs, isolated from everything else.
- **Remaining prompt-quality contamination: 7.8 percentage points, 95% CI [2.0pp, 12.8pp]**
  (published hi-en vs. the routing-corrected held-out score) — real, CI still excludes zero, but
  visibly weaker and noisier once misrouting is factored out.
- These two approximately sum to the raw, undecomposed gap (4.6 + 7.8 = 12.4pp ≈ the
  directly-measured 12.3pp) — internally consistent, not two independent claims that happen to
  add up by coincidence.

A companion finding worth stating plainly rather than only in an appendix: a 3-judge panel's
inter-rater agreement on the `en`/`hi-en` language boundary itself is **0.380** (Krippendorff's
alpha, n=106) — the lowest of any field this project scores by a wide margin (sentiment 0.942,
buy_again 1.000), and low symmetrically for every judge pair, not a bias in one direction. This
converges, from a completely independent method, with the production detector's own 48%
disagreement rate against the corpus's language labels. Three independent methods — a
corpus-mining heuristic, a production detector, and a three-judge panel — cannot agree on where
"some Hindi words in an English review" becomes "a Hinglish review." Our recommendation (not yet
a decision — see ADR 0023) is that the fix for this isn't a sharper definition of the boundary,
which the evidence above suggests may not exist to be found — it's reducing how much the
system's correctness depends on drawing that line at all, for instance by evaluating whether a
single unified prompt can serve both cases without a binary routing decision in between.

## Why publish the failures alongside the finding

Every failure documented above was found by someone on this project actively looking for a
reason their own result might be wrong, not by an external auditor. That is the point. A
contamination measurement that only reports its final number invites exactly the kind of blind
trust that produced the contamination in the first place. Reporting the self-judging panel, the
degenerate hedger, and the false-unanimity bug alongside the 12.3pp finding is not an admission
of unreliability — it's the actual evidence that the number that remains has been checked hard
enough to be worth trusting. A number is either derived by a process that survives someone trying
to break it, or it hasn't really been checked at all. This project's failures are the credibility,
not a liability to be hidden alongside the number they helped produce.

## Provenance

- ADR 0013: consensus panel self-judging contamination.
- ADR 0016: third judge, degenerate-hedger finding.
- ADR 0018: first uncontaminated measurement (67.9%, n=23, live HTTP, superseded).
- ADR 0019: held-out corpus complete, language-boundary alpha=0.380 finding.
- ADR 0020: unanimous-requires-full-panel fix.
- ADR 0021: reproducible measurement (this session), misrouting/contamination decomposition.
- ADR 0022: Devanagari Hindi retirement.
- ADR 0023: language-boundary abstraction recommendation.

All numbers above are sourced to the ADRs listed and their underlying artifacts
(`eval/results/held_out_scoring_v2.json`, `eval/cassettes/held_out_cassettes.json`,
`eval/consensus/results/consensus_labels.jsonl`) — none are hand-typed estimates.
